from __future__ import annotations

import asyncio

import pytest

from agent_resilience.aws_store import AWSDurableStore
from agent_resilience.decision import DeterministicDecisionEngine
from agent_resilience.errors import RetryableWorkflowError
from agent_resilience.loop_detector import LoopDetector
from agent_resilience.models import ToolName, WorkflowState, WorkflowStatus
from agent_resilience.runtime import WorkflowRuntime
from agent_resilience.tools import ScenarioBackend, ToolGateway
from agent_resilience.worker import DurableWorker


pytestmark = pytest.mark.integration


class NoTransientScenarioBackend(ScenarioBackend):
    def __init__(self):
        super().__init__()
        self.scenarios["checkout-pool-exhaustion"]["transient_failures"] = {}


class AlwaysFailBackend(NoTransientScenarioBackend):
    def execute(self, state, tool, arguments, attempt):
        raise RetryableWorkflowError("injected persistent downstream outage")


def build(config, backend=None):
    store = AWSDurableStore(config)
    runtime = WorkflowRuntime(
        store,
        DeterministicDecisionEngine(),
        ToolGateway(store, backend or NoTransientScenarioBackend()),
        LoopDetector(),
    )
    return store, DurableWorker(store, runtime, config, "replacement-worker")


@pytest.mark.asyncio
async def test_worker_crash_after_step_three_resumes_from_step_four(aws_settings):
    store, worker = build(aws_settings)
    state = WorkflowState(task_id="incident-chaos", goal="Investigate checkout failures and recover safely")
    store.create_workflow(state, aws_settings.max_queue_attempts)

    for _ in range(3):
        assert await worker.run_once()
    before_crash = store.get_workflow(state.task_id)
    assert before_crash.completed_steps == ["read_alert", "inspect_metrics", "query_logs"]

    abandoned = store.claim("crashed-worker", lease_seconds=1)
    assert abandoned and abandoned.task_id == state.task_id
    await asyncio.sleep(1.2)

    assert await worker.run_once()
    resumed = store.get_workflow(state.task_id)
    assert resumed.completed_steps == ["read_alert", "inspect_metrics", "query_logs", "dependency_health"]
    assert resumed.tool_calls == 4
    assert any(event.event_type == "TOOL_COMPLETED" for event in store.list_events(state.task_id))


@pytest.mark.asyncio
async def test_repeated_failures_are_explicitly_dead_lettered(aws_settings):
    aws_settings.max_queue_attempts = 2
    store, worker = build(aws_settings, AlwaysFailBackend())
    state = WorkflowState(task_id="incident-dlq", goal="Investigate a persistent downstream failure")
    store.create_workflow(state, 2)
    assert await worker.run_once()
    assert await worker.run_once()
    failed = store.get_workflow(state.task_id)
    assert failed.status == WorkflowStatus.DEAD_LETTERED
    assert store.queue_counts()["DEAD"] >= 1


def test_completed_side_effect_is_replayed_from_idempotency_ledger(aws_settings):
    class CountingBackend(NoTransientScenarioBackend):
        calls = 0

        def execute(self, state, tool, arguments, attempt):
            if tool == ToolName.RESTART_SERVICE:
                self.calls += 1
            return super().execute(state, tool, arguments, attempt)

    store = AWSDurableStore(aws_settings)
    backend = CountingBackend()
    gateway = ToolGateway(store, backend)
    state = WorkflowState(task_id="incident-idempotent", goal="Safely restart checkout after approval")
    arguments = {"service": "checkout-service", "environment": "production"}
    first, cached_first = gateway.execute(state, ToolName.RESTART_SERVICE, arguments)
    second, cached_second = gateway.execute(state, ToolName.RESTART_SERVICE, arguments)
    assert first == second
    assert not cached_first and cached_second
    assert backend.calls == 1
