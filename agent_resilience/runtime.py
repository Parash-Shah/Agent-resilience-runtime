from __future__ import annotations

import hashlib
import json
import time

from .decision import DecisionEngine
from .contracts import RuntimeStore
from .errors import PermanentWorkflowError, RetryableWorkflowError
from .loop_detector import LoopDetector
from .metrics import DECISION_LATENCY, MODEL_CALLS, TOOL_CALLS, WORKFLOW_FAILURES, WORKFLOWS_COMPLETED, emit_metric
from .models import PendingAction, WorkflowState, WorkflowStatus
from .tools import ToolGateway


class WorkflowRuntime:
    def __init__(self, store: RuntimeStore, engine: DecisionEngine, gateway: ToolGateway, loop_detector: LoopDetector | None = None):
        self.store = store
        self.engine = engine
        self.gateway = gateway
        self.loop_detector = loop_detector or LoopDetector()

    async def process(self, task_id: str) -> WorkflowState:
        state = self._required_state(task_id)
        if state.terminal() or state.status == WorkflowStatus.WAITING_FOR_APPROVAL:
            return state
        if state.pending_action:
            return self._resume_approved_action(state)

        state.status = WorkflowStatus.RUNNING
        state.last_error = None
        state = self.store.save_workflow(state, state.version)
        self.store.record_event(task_id, "AGENT_DECISION_STARTED", {"model_call": state.model_calls + 1})

        started = time.monotonic()
        try:
            decision = await self.engine.decide(state)
            MODEL_CALLS.labels("success").inc()
        except RetryableWorkflowError as error:
            MODEL_CALLS.labels("failure").inc()
            state.retries += 1
            state.status = WorkflowStatus.QUEUED
            state.last_error = str(error)
            state = self.store.save_workflow(state, state.version)
            self.store.record_event(task_id, "MODEL_RETRYABLE_FAILURE", {"error": str(error)})
            emit_metric("ModelFailures", outcome="retryable")
            raise
        except Exception:
            MODEL_CALLS.labels("failure").inc()
            raise
        finally:
            elapsed = time.monotonic() - started
            DECISION_LATENCY.observe(elapsed)
            emit_metric("DecisionLatency", elapsed)

        state.model_calls += 1
        self.store.record_event(task_id, "AGENT_DECISION", decision.model_dump(mode="json"))
        if decision.diagnosis:
            state.diagnosis = decision.diagnosis
        if decision.remediation:
            state.remediation = decision.remediation

        if decision.action == "complete":
            state.status = WorkflowStatus.COMPLETED
            state.final_answer = decision.final_answer
            state.current_step = None
            state = self.store.save_workflow(state, state.version)
            self.store.record_event(task_id, "WORKFLOW_COMPLETED", {"answer": state.final_answer})
            WORKFLOWS_COMPLETED.inc()
            emit_metric("WorkflowsCompleted")
            return state
        if decision.action == "fail":
            return self._fail(state, decision.rationale, "agent_decision")

        assert decision.tool is not None
        requested_arguments = decision.arguments.model_dump(exclude_none=True)
        arguments, policy = self.gateway.validate_and_decide(decision.tool, requested_arguments)
        state.current_step = decision.tool.value
        state.tool_history.append(decision.tool.value)
        state.progress_history.append(self._progress_fingerprint(state))
        loop_reason = self.loop_detector.reason(state.tool_history, state.progress_history)
        if loop_reason:
            state.status = WorkflowStatus.LOOP_STOPPED
            state.last_error = loop_reason
            state = self.store.save_workflow(state, state.version)
            self.store.record_event(task_id, "LOOP_DETECTED", {"reason": loop_reason, "history": state.tool_history[-12:]})
            WORKFLOW_FAILURES.labels("loop").inc()
            emit_metric("LoopDetections")
            return state
        state = self.store.save_workflow(state, state.version)

        if not policy.allowed:
            self.store.record_event(task_id, "POLICY_DENIED", {"tool": decision.tool.value, "reason": policy.reason})
            return self._fail(state, policy.reason, "policy")
        action_id = self.gateway.action_id(state, decision.tool, arguments)
        if policy.approval_required:
            state.pending_action = PendingAction(
                action_id=action_id, tool=decision.tool, arguments=arguments,
                risk=policy.risk, rationale=decision.rationale,
            )
            state.status = WorkflowStatus.WAITING_FOR_APPROVAL
            state = self.store.save_workflow(state, state.version)
            self.store.create_approval(task_id, action_id)
            self.store.record_event(task_id, "APPROVAL_REQUIRED", state.pending_action.model_dump(mode="json"))
            return state
        return self._execute_tool(state, decision.tool, arguments)

    def _resume_approved_action(self, state: WorkflowState) -> WorkflowState:
        pending = state.pending_action
        assert pending is not None
        approval = self.store.approval_status(state.task_id, pending.action_id)
        if approval != "APPROVED":
            state.status = WorkflowStatus.WAITING_FOR_APPROVAL
            return state
        self.store.record_event(state.task_id, "APPROVED_ACTION_RESUMED", {"action_id": pending.action_id})
        return self._execute_tool(state, pending.tool, pending.arguments)

    def _execute_tool(self, state: WorkflowState, tool, arguments: dict) -> WorkflowState:
        try:
            result, cached = self.gateway.execute(state, tool, arguments)
            TOOL_CALLS.labels(tool.value, "cached" if cached else "success").inc()
            emit_metric("ToolCalls", tool=tool.value, outcome="cached" if cached else "success")
        except RetryableWorkflowError as error:
            TOOL_CALLS.labels(tool.value, "retryable_failure").inc()
            emit_metric("ToolFailures", tool=tool.value, outcome="retryable")
            state.retries += 1
            state.last_error = str(error)
            state.status = WorkflowStatus.QUEUED
            self.store.save_workflow(state, state.version)
            self.store.record_event(state.task_id, "TOOL_RETRYABLE_FAILURE", {"tool": tool.value, "error": str(error)})
            raise
        except PermanentWorkflowError as error:
            TOOL_CALLS.labels(tool.value, "permanent_failure").inc()
            emit_metric("ToolFailures", tool=tool.value, outcome="permanent")
            return self._fail(state, str(error), "tool")

        state.tool_calls += 1
        state.evidence[tool.value] = result
        if tool.value not in state.completed_steps:
            state.completed_steps.append(tool.value)
        state.pending_action = None
        state.current_step = None
        state.last_error = None
        state.status = WorkflowStatus.QUEUED
        state.progress_history.append(self._progress_fingerprint(state))
        state = self.store.save_workflow(state, state.version)
        self.store.record_event(
            state.task_id, "TOOL_COMPLETED",
            {"tool": tool.value, "cached": cached, "result": result},
        )
        return state

    def _fail(self, state: WorkflowState, reason: str, category: str) -> WorkflowState:
        state.status = WorkflowStatus.FAILED
        state.last_error = reason
        state.current_step = None
        state = self.store.save_workflow(state, state.version)
        self.store.record_event(state.task_id, "WORKFLOW_FAILED", {"category": category, "reason": reason})
        WORKFLOW_FAILURES.labels(category).inc()
        emit_metric("WorkflowFailures", category=category)
        return state

    def _required_state(self, task_id: str) -> WorkflowState:
        state = self.store.get_workflow(task_id)
        if state is None:
            raise PermanentWorkflowError(f"unknown workflow: {task_id}")
        return state

    @staticmethod
    def _progress_fingerprint(state: WorkflowState) -> str:
        material = json.dumps(state.evidence, sort_keys=True, default=str)
        return hashlib.sha256(material.encode()).hexdigest()[:16]
