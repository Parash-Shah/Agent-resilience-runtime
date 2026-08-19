from __future__ import annotations

import asyncio
import hmac
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings, settings
from .decision import DecisionEngine, build_decision_engine
from .loop_detector import LoopDetector
from .metrics import APPROVALS, WORKFLOWS_CREATED
from .models import ApprovalRequest, CreateIncidentRequest, WorkflowState, WorkflowStatus
from .observability import configure_otel
from .runtime import WorkflowRuntime
from .store import SQLiteStore
from .tools import ToolGateway
from .worker import DurableWorker


def create_app(config: Settings = settings, engine: DecisionEngine | None = None) -> FastAPI:
    store = SQLiteStore(config.database_path)
    runtime = WorkflowRuntime(store, engine or build_decision_engine(config), ToolGateway(store), LoopDetector())
    worker = DurableWorker(store, runtime, config)
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
        version="0.2.0",
        description="Durable, policy-bound execution for autonomous incident response agents.",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.worker = worker
    configure_otel(app, config)

    async def require_admin(authorization: str | None = Header(default=None)) -> None:
        if not config.admin_api_token:
            return
        expected = f"Bearer {config.admin_api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="valid administrator bearer token required")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "agent_mode": config.agent_mode, "queue": store.queue_counts()}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/incidents", response_model=WorkflowState, status_code=status.HTTP_202_ACCEPTED)
    async def create_incident(request: CreateIncidentRequest) -> WorkflowState:
        task_id = f"incident-{uuid.uuid4().hex[:12]}"
        state = WorkflowState(task_id=task_id, goal=request.goal, scenario_id=request.scenario_id)
        store.create_workflow(state, config.max_queue_attempts)
        WORKFLOWS_CREATED.inc()
        return state

    @app.get("/v1/incidents/{task_id}", response_model=WorkflowState)
    async def get_incident(task_id: str) -> WorkflowState:
        return _required(store, task_id)

    @app.get("/v1/incidents/{task_id}/events")
    async def get_events(task_id: str):
        _required(store, task_id)
        return store.list_events(task_id)

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
        return state

    @app.post("/internal/worker/run-once", dependencies=[Depends(require_admin)])
    async def run_worker_once() -> dict:
        return {"processed": await worker.run_once()}

    return app


def _required(store: SQLiteStore, task_id: str) -> WorkflowState:
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
