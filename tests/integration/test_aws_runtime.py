from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys

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

    environment = os.environ.copy()
    environment.update({
        "AWS_DEFAULT_REGION": aws_settings.aws_region,
        "LOCALSTACK_ENDPOINT": aws_settings.aws_endpoint_url,
        "DYNAMODB_TABLE_NAME": aws_settings.dynamodb_table_name,
        "SQS_QUEUE_URL": aws_settings.sqs_queue_url,
        "SQS_DLQ_URL": aws_settings.sqs_dlq_url,
    })
    helper = Path(__file__).with_name("chaos_worker.py")
    process = subprocess.Popen([sys.executable, str(helper)], env=environment)
    try:
        for _ in range(200):
            before_crash = store.get_workflow(state.task_id)
            if (
                before_crash.completed_steps == ["read_alert", "inspect_metrics", "query_logs"]
                and before_crash.current_step == "dependency_health"
            ):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("subprocess worker did not reach the step-four chaos window")
        process.terminate()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    assert any(event.event_type == "CHAOS_PAUSE_STARTED" for event in store.list_events(state.task_id))
    await asyncio.sleep(2.5)

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
    dead = store.list_dead_letters()
    assert dead and dead[0].task_id == state.task_id
    replayed = store.replay_dead_letter(dead[0].id, state.task_id, 3, "integration-test", "outage resolved")
    assert replayed and replayed.status == WorkflowStatus.QUEUED
    assert any(event.event_type == "DLQ_REPLAYED" for event in store.list_events(state.task_id))


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
