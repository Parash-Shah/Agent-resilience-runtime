from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import socket
import uuid

from .config import Settings, settings
from .decision import build_decision_engine
from .contracts import RuntimeStore
from .errors import PermanentWorkflowError, RetryableWorkflowError
from .loop_detector import LoopDetector
from .factory import build_store, build_tool_backend
from .metrics import QUEUE_DELIVERIES, QUEUE_DEPTH, WORKFLOW_FAILURES, configure_cloudwatch_metrics, emit_metric
from .models import WorkflowStatus
from .runtime import WorkflowRuntime
from .tools import ToolGateway


class DurableWorker:
    def __init__(self, store: RuntimeStore, runtime: WorkflowRuntime, config: Settings, worker_id: str | None = None):
        self.store = store
        self.runtime = runtime
        self.config = config
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    async def run_once(self) -> bool:
        delivery = self.store.claim(self.worker_id, self.config.queue_lease_seconds)
        if delivery is None:
            self._update_depth()
            return False
        heartbeat = asyncio.create_task(self._heartbeat(delivery))
        try:
            state = await self.runtime.process(delivery.task_id)
            self.store.acknowledge(delivery.id)
            QUEUE_DELIVERIES.labels("success").inc()
            emit_metric("QueueDeliveries", outcome="success")
            if state.status == WorkflowStatus.QUEUED:
                self.store.enqueue(state.task_id, self.config.max_queue_attempts)
        except RetryableWorkflowError as error:
            dead = self.store.retry_or_dead_letter(delivery, str(error), self.config.queue_retry_base_seconds)
            QUEUE_DELIVERIES.labels("dead_letter" if dead else "retry").inc()
            emit_metric("QueueDeliveries", outcome="dead_letter" if dead else "retry")
            if dead:
                state = self.store.get_workflow(delivery.task_id)
                if state and not state.terminal():
                    state.status = WorkflowStatus.DEAD_LETTERED
                    state.last_error = str(error)
                    self.store.save_workflow(state, state.version)
                    self.store.record_event(state.task_id, "DEAD_LETTERED", {"error": str(error)})
                    WORKFLOW_FAILURES.labels("dead_letter").inc()
                    emit_metric("WorkflowFailures", category="dead_letter")
        except PermanentWorkflowError as error:
            self.store.acknowledge(delivery.id)
            state = self.store.get_workflow(delivery.task_id)
            if state and not state.terminal():
                state.status = WorkflowStatus.FAILED
                state.last_error = str(error)
                self.store.save_workflow(state, state.version)
                self.store.record_event(state.task_id, "WORKFLOW_FAILED", {"category": "permanent", "reason": str(error)})
            QUEUE_DELIVERIES.labels("permanent_failure").inc()
            emit_metric("QueueDeliveries", outcome="permanent_failure")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        self._update_depth()
        return True

    async def _heartbeat(self, delivery) -> None:
        interval = max(0.1, min(self.config.queue_heartbeat_seconds, self.config.queue_lease_seconds / 2))
        while True:
            await asyncio.sleep(interval)
            self.store.extend_lease(delivery, self.config.queue_lease_seconds)
            self.store.record_event(
                delivery.task_id,
                "QUEUE_LEASE_EXTENDED",
                {"worker_id": self.worker_id, "lease_seconds": self.config.queue_lease_seconds},
            )

    async def serve(self) -> None:
        while not self._stop.is_set():
            processed = await self.run_once()
            if not processed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.config.worker_poll_seconds)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()

    def _update_depth(self) -> None:
        for status, count in self.store.queue_counts().items():
            QUEUE_DEPTH.labels(status).set(count)
            emit_metric("QueueDepth", float(count), status=status)


def build_worker(config: Settings = settings) -> DurableWorker:
    configure_cloudwatch_metrics(config)
    store = build_store(config)
    runtime = WorkflowRuntime(
        store,
        build_decision_engine(config),
        ToolGateway(store, build_tool_backend(config)),
        LoopDetector(),
        config.chaos_pause_tool,
        config.chaos_pause_after_steps,
        config.chaos_pause_seconds,
    )
    return DurableWorker(store, runtime, config)


def run() -> None:
    asyncio.run(build_worker().serve())
