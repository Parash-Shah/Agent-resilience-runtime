from __future__ import annotations

import asyncio
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings, settings
from .decision import DecisionEngine, build_decision_engine
from .contracts import RuntimeStore
from .factory import build_store, build_tool_backend
from .loop_detector import LoopDetector
from .metrics import APPROVALS, WORKFLOWS_CREATED, configure_cloudwatch_metrics, emit_metric
from .models import ApprovalRequest, CreateIncidentRequest, ReplayDeadLetterRequest, WorkflowState, WorkflowStatus
from .observability import configure_otel
from .runtime import WorkflowRuntime
from .tools import ToolGateway
from .worker import DurableWorker


def create_app(config: Settings = settings, engine: DecisionEngine | None = None) -> FastAPI:
    if config.runtime_backend == "aws" and (not config.admin_api_token or not config.viewer_api_token):
        raise ValueError("AWS runtime requires ADMIN_API_TOKEN and VIEWER_API_TOKEN")
    store = build_store(config)
    runtime = WorkflowRuntime(
        store,
        engine or build_decision_engine(config),
        ToolGateway(store, build_tool_backend(config)),
        LoopDetector(),
        config.chaos_pause_tool,
        config.chaos_pause_after_steps,
        config.chaos_pause_seconds,
    )
    worker = DurableWorker(store, runtime, config)
    configure_cloudwatch_metrics(config)
    worker_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal worker_task
        if config.run_worker:
            worker_task = asyncio.create_task(worker.serve())
        yield
        if worker_task:
            worker.stop()
            await worker_task

    app = FastAPI(
        title="AgentResilience",
        version="1.0.0",
        description="Durable, policy-bound execution for autonomous incident response agents.",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.worker = worker
    configure_otel(app, config)

    static_path = Path(__file__).with_name("static")
    app.mount("/assets", StaticFiles(directory=static_path), name="assets")

    async def require_admin(authorization: str | None = Header(default=None)) -> None:
        if not config.admin_api_token:
            return
        expected = f"Bearer {config.admin_api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="valid administrator bearer token required")

    async def require_viewer(authorization: str | None = Header(default=None)) -> str:
        if not config.admin_api_token and not config.viewer_api_token:
            return "administrator"
        candidates = {
            "administrator": config.admin_api_token,
            "viewer": config.viewer_api_token,
        }
        for role, token in candidates.items():
            if token and authorization and hmac.compare_digest(authorization, f"Bearer {token}"):
                return role
        raise HTTPException(status_code=401, detail="valid control-plane bearer token required")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "agent_mode": config.agent_mode, "queue": store.queue_counts()}

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_path / "index.html")

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post(
        "/v1/incidents",
        response_model=WorkflowState,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    async def create_incident(request: CreateIncidentRequest) -> WorkflowState:
        task_id = f"incident-{uuid.uuid4().hex[:12]}"
        state = WorkflowState(task_id=task_id, goal=request.goal, scenario_id=request.scenario_id)
        store.create_workflow(state, config.max_queue_attempts)
        WORKFLOWS_CREATED.inc()
        emit_metric("WorkflowsCreated")
        return state

    @app.get("/v1/incidents/{task_id}", response_model=WorkflowState, dependencies=[Depends(require_viewer)])
    async def get_incident(task_id: str) -> WorkflowState:
        return _required(store, task_id)

    @app.get("/v1/incidents/{task_id}/events", dependencies=[Depends(require_viewer)])
    async def get_events(task_id: str):
        _required(store, task_id)
        return store.list_events(task_id)

    @app.get("/v1/dashboard/session")
    async def dashboard_session(role: str = Depends(require_viewer)) -> dict:
        return {"role": role}

    @app.get("/v1/dashboard/incidents", dependencies=[Depends(require_viewer)])
    async def list_incidents(
        limit: int = Query(default=100, ge=1, le=500),
        workflow_status: WorkflowStatus | None = Query(default=None, alias="status"),
    ) -> list[WorkflowState]:
        return store.list_workflows(limit, workflow_status)

    @app.get("/v1/dashboard/summary", dependencies=[Depends(require_viewer)])
    async def dashboard_summary() -> dict:
        workflows = store.list_workflows(500)
        terminal = [item for item in workflows if item.terminal()]
        completed = [item for item in terminal if item.status == WorkflowStatus.COMPLETED]
        latencies = sorted((item.updated_at - item.created_at).total_seconds() for item in terminal)
        events = [event for item in workflows for event in store.list_events(item.task_id)]
        status_counts = {
            item.value: sum(workflow.status == item for workflow in workflows)
            for item in WorkflowStatus
        }
        return {
            "total_incidents": len(workflows),
            "active_incidents": sum(not item.terminal() for item in workflows),
            "success_rate": round(len(completed) / len(terminal), 4) if terminal else 0.0,
            "total_retries": sum(item.retries for item in workflows),
            "model_tokens": sum(item.input_tokens + item.output_tokens for item in workflows),
            "tool_failures": sum(
                event.event_type == "TOOL_RETRYABLE_FAILURE"
                or (event.event_type == "WORKFLOW_FAILED" and event.payload.get("category") == "tool")
                for event in events
            ),
            "loop_detections": status_counts[WorkflowStatus.LOOP_STOPPED.value],
            "average_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "p95_latency_seconds": latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0,
            "queue": store.queue_counts(),
            "statuses": status_counts,
        }

    @app.get("/v1/dashboard/dead-letters", dependencies=[Depends(require_viewer)])
    async def dead_letters(limit: int = Query(default=25, ge=1, le=100)):
        return store.list_dead_letters(limit)

    @app.post("/v1/dashboard/dead-letters/replay", response_model=WorkflowState, dependencies=[Depends(require_admin)])
    async def replay_dead_letter(request: ReplayDeadLetterRequest) -> WorkflowState:
        replayed = store.replay_dead_letter(
            request.delivery_id,
            request.task_id,
            config.max_queue_attempts,
            request.actor,
            request.reason,
        )
        if replayed is None:
            raise HTTPException(status_code=404, detail="dead-letter delivery not found")
        return replayed

    @app.get("/v1/dashboard/incidents/{task_id}/stream", dependencies=[Depends(require_viewer)])
    async def stream_incident(task_id: str, request: Request) -> StreamingResponse:
        _required(store, task_id)

        async def snapshots():
            previous = None
            while not await request.is_disconnected():
                state = _required(store, task_id)
                events = store.list_events(task_id)
                fingerprint = (state.version, len(events))
                if fingerprint != previous:
                    payload = {
                        "state": state.model_dump(mode="json"),
                        "events": [event.model_dump(mode="json") for event in events],
                    }
                    yield f"event: incident\ndata: {json.dumps(payload)}\n\n"
                    previous = fingerprint
                if state.terminal():
                    break
                yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            snapshots(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/incidents/{task_id}/approve", response_model=WorkflowState, dependencies=[Depends(require_admin)])
    async def approve(task_id: str, request: ApprovalRequest) -> WorkflowState:
        state = _required(store, task_id)
        if state.status != WorkflowStatus.WAITING_FOR_APPROVAL or not state.pending_action:
            raise HTTPException(status_code=409, detail="workflow is not waiting for approval")
        store.resolve_approval(task_id, state.pending_action.action_id, True, request.actor, request.reason)
        state.status = WorkflowStatus.QUEUED
        state = store.save_workflow(state, state.version)
        store.record_event(task_id, "APPROVAL_GRANTED", {"actor": request.actor, "reason": request.reason})
        store.enqueue(task_id, config.max_queue_attempts)
        APPROVALS.labels("approved").inc()
        emit_metric("Approvals", decision="approved")
        return state

    @app.post("/v1/incidents/{task_id}/reject", response_model=WorkflowState, dependencies=[Depends(require_admin)])
    async def reject(task_id: str, request: ApprovalRequest) -> WorkflowState:
        state = _required(store, task_id)
        if state.status != WorkflowStatus.WAITING_FOR_APPROVAL or not state.pending_action:
            raise HTTPException(status_code=409, detail="workflow is not waiting for approval")
        store.resolve_approval(task_id, state.pending_action.action_id, False, request.actor, request.reason)
        state.status = WorkflowStatus.HUMAN_REJECTED
        state.last_error = request.reason or "human rejected the proposed action"
        state.pending_action = None
        state = store.save_workflow(state, state.version)
        store.record_event(task_id, "APPROVAL_REJECTED", {"actor": request.actor, "reason": request.reason})
        APPROVALS.labels("rejected").inc()
        emit_metric("Approvals", decision="rejected")
        return state

    @app.post("/internal/worker/run-once", dependencies=[Depends(require_admin)])
    async def run_worker_once() -> dict:
        return {"processed": await worker.run_once()}

    return app


def _required(store: RuntimeStore, task_id: str) -> WorkflowState:
    state = store.get_workflow(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return state


def run() -> None:
    uvicorn.run(
        "agent_resilience.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
