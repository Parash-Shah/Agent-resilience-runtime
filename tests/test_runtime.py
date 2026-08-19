from __future__ import annotations

import pytest

from agent_resilience.decision import DeterministicDecisionEngine
from agent_resilience.loop_detector import LoopDetector
from agent_resilience.models import WorkflowState, WorkflowStatus
from agent_resilience.runtime import WorkflowRuntime
from agent_resilience.store import SQLiteStore
from agent_resilience.tools import ScenarioBackend, ToolGateway
from agent_resilience.worker import DurableWorker


def build(test_settings):
    store = SQLiteStore(test_settings.database_path)
    runtime = WorkflowRuntime(store, DeterministicDecisionEngine(), ToolGateway(store, ScenarioBackend()), LoopDetector())
    worker = DurableWorker(store, runtime, test_settings, "test-worker")
    return store, worker


@pytest.mark.asyncio
async def test_workflow_retries_resumes_waits_for_approval_and_completes(test_settings):
    store, worker = build(test_settings)
    initial = WorkflowState(
        task_id="incident-e2e", goal="Investigate why checkout-service has elevated production errors"
    )
    store.create_workflow(initial, test_settings.max_queue_attempts)

    for _ in range(12):
        await worker.run_once()
        state = store.get_workflow(initial.task_id)
        assert state
        if state.status == WorkflowStatus.WAITING_FOR_APPROVAL:
            break
    assert state.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert state.completed_steps == ["read_alert", "inspect_metrics", "query_logs", "dependency_health"]
    assert state.retries == 1
    assert state.pending_action

    store.resolve_approval(state.task_id, state.pending_action.action_id, True, "tester", "safe remediation")
    state.status = WorkflowStatus.QUEUED
    store.save_workflow(state, state.version)
    store.enqueue(state.task_id, test_settings.max_queue_attempts)

    for _ in range(8):
        await worker.run_once()
        state = store.get_workflow(initial.task_id)
        assert state
        if state.terminal():
            break
    assert state.status == WorkflowStatus.COMPLETED
    assert state.diagnosis == "database connection pool exhaustion"
    assert state.evidence["verify_recovery"]["error_rate_percent"] == 0.3
    events = [event.event_type for event in store.list_events(state.task_id)]
    assert "TOOL_RETRYABLE_FAILURE" in events
    assert "APPROVAL_REQUIRED" in events
    assert "WORKFLOW_COMPLETED" in events


@pytest.mark.asyncio
async def test_human_rejection_is_terminal(test_settings):
    store, worker = build(test_settings)
    state = WorkflowState(task_id="incident-reject", goal="Investigate checkout production failures")
    store.create_workflow(state, 3)
    for _ in range(12):
        await worker.run_once()
        state = store.get_workflow(state.task_id)
        if state and state.status == WorkflowStatus.WAITING_FOR_APPROVAL:
            break
    assert state and state.pending_action
    store.resolve_approval(state.task_id, state.pending_action.action_id, False, "tester", "risk too high")
    state.status = WorkflowStatus.HUMAN_REJECTED
    state.pending_action = None
    store.save_workflow(state, state.version)
    assert (store.get_workflow(state.task_id)).terminal()
