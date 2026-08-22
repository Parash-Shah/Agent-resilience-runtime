from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any

from .config import Settings
from .errors import PermanentWorkflowError, RetryableWorkflowError
from .models import AgentDecision, ToolName, WorkflowState


ORCHESTRATOR_INSTRUCTIONS = """
You are the orchestrator for a production incident response runtime. Decide exactly one next action.
You do not execute infrastructure operations directly; choose one bounded tool for the runtime gateway.
Gather alert, metrics, logs, and dependency evidence as needed. Do not repeat a tool unless prior evidence is
missing or explicitly inconclusive. Production restarts require human approval and database deletion is forbidden.
When evidence supports a bounded production restart, request it by returning use_tool with restart_service; the
runtime, not you, creates the approval gate and pauses execution. Do not return fail merely because approval is
required. After restart_service is completed, use verify_recovery before completing the incident.
Complete only when you can state a supported diagnosis, remediation, and recovery result. Return the typed output.
Use specialist agents when their bounded analysis improves your decision.
""".strip()


class DecisionEngine(ABC):
    @abstractmethod
    async def decide(self, state: WorkflowState) -> AgentDecision: ...


class DeterministicDecisionEngine(DecisionEngine):
    """Offline reference policy used by tests and reliability fault injection."""

    async def decide(self, state: WorkflowState) -> AgentDecision:
        defaults = {"service": "checkout-service", "environment": "production"}
        sequence = [
            ("read_alert", ToolName.READ_ALERT),
            ("inspect_metrics", ToolName.INSPECT_METRICS),
            ("query_logs", ToolName.QUERY_LOGS),
            ("dependency_health", ToolName.DEPENDENCY_HEALTH),
        ]
        for key, tool in sequence:
            if key not in state.evidence:
                return AgentDecision(action="use_tool", tool=tool, arguments=defaults, rationale=f"collect {key} evidence")
        if "restart_service" not in state.completed_steps:
            return AgentDecision(
                action="use_tool", tool=ToolName.RESTART_SERVICE, arguments=defaults,
                rationale="evidence supports a bounded service restart",
                diagnosis="database connection pool exhaustion",
            )
        if "verify_recovery" not in state.evidence:
            return AgentDecision(action="use_tool", tool=ToolName.VERIFY_RECOVERY, arguments=defaults, rationale="verify recovery")
        verification = state.evidence["verify_recovery"]
        metrics = verification.get("metrics", verification)
        rate = metrics.get("error_rate_percent")
        recovered = rate is not None and float(rate) < 1.0
        if recovered:
            return AgentDecision(
                action="complete", rationale="recovery evidence meets threshold",
                diagnosis="database connection pool exhaustion",
                remediation="checkout-service restarted after approval",
                final_answer="Checkout recovered: error rate is below 1% after the approved restart.",
            )
        return AgentDecision(
            action="fail", rationale="restart did not restore service health",
            diagnosis="upstream dependency failure", remediation="escalate to the dependency owner",
        )


class OpenAIDecisionEngine(DecisionEngine):
    def __init__(self, config: Settings):
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for live agent mode")
        from agents import Agent, set_default_openai_key, set_tracing_export_api_key

        self.config = config
        set_default_openai_key(config.openai_api_key)
        set_tracing_export_api_key(config.openai_api_key)
        log_specialist = Agent(
            name="Log analysis specialist", model=config.openai_model,
            instructions="Analyze supplied application-log evidence. Identify concrete error signatures and avoid speculation.",
        )
        cloud_specialist = Agent(
            name="Cloud state specialist", model=config.openai_model,
            instructions="Analyze supplied metrics and dependency evidence. Distinguish service-local from downstream failures.",
        )
        remediation_specialist = Agent(
            name="Remediation specialist", model=config.openai_model,
            instructions="Recommend the least risky remediation supported by evidence. Never bypass approval or policy.",
        )
        self.agent = Agent(
            name="Incident orchestrator",
            model=config.openai_model,
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            output_type=AgentDecision,
            tools=[
                log_specialist.as_tool(tool_name="analyze_logs", tool_description="Analyze collected log evidence."),
                cloud_specialist.as_tool(tool_name="analyze_cloud_state", tool_description="Analyze metrics and dependencies."),
                remediation_specialist.as_tool(tool_name="review_remediation", tool_description="Review a proposed safe remediation."),
            ],
        )

    async def decide(self, state: WorkflowState) -> AgentDecision:
        from agents import Runner, trace

        prompt = self._prompt(state)
        try:
            with trace("agent-resilience-incident", group_id=state.task_id):
                result = await Runner.run(self.agent, prompt, max_turns=self.config.max_agent_turns)
            if not isinstance(result.final_output, AgentDecision):
                raise RetryableWorkflowError("agent returned invalid structured output")
            return result.final_output
        except RetryableWorkflowError:
            raise
        except Exception as error:
            raise self.classify_error(error) from error

    @staticmethod
    def classify_error(error: Exception) -> PermanentWorkflowError | RetryableWorkflowError:
        from agents.exceptions import UserError
        from openai import AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError

        name = type(error).__name__
        message = f"OpenAI agent run failed ({name}): {error}"
        if isinstance(error, (UserError, AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError)):
            return PermanentWorkflowError(message)
        return RetryableWorkflowError(message)

    def _prompt(self, state: WorkflowState) -> str:
        evidence = json.dumps(state.evidence, sort_keys=True, default=str)
        compacted = evidence[-self.config.max_evidence_chars:]
        available = [tool.value for tool in ToolName]
        return json.dumps(
            {
                "goal": state.goal,
                "status": state.status,
                "completed_steps": state.completed_steps,
                "tool_history": state.tool_history[-20:],
                "evidence": compacted,
                "available_tools": available,
                "required_tool_arguments": {"service": "checkout-service", "environment": "production"},
            },
            default=str,
        )


def build_decision_engine(config: Settings) -> DecisionEngine:
    return DeterministicDecisionEngine() if config.agent_mode == "deterministic" else OpenAIDecisionEngine(config)
