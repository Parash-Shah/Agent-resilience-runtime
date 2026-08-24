from fastapi.testclient import TestClient
import pytest

from agent_resilience.api import create_app
from agent_resilience.config import Settings
from agent_resilience.decision import DeterministicDecisionEngine
from agent_resilience.models import WorkflowStatus


def test_api_exposes_health_incident_and_events(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post(
            "/v1/incidents",
            json={"goal": "An unauthenticated incident must not be created"},
        ).status_code == 401
        created = client.post(
            "/v1/incidents",
            headers=headers,
            json={"goal": "Investigate why checkout-service has elevated production errors"},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        assert client.get(f"/v1/incidents/{task_id}").status_code == 401
        assert client.get(f"/v1/incidents/{task_id}", headers=headers).status_code == 200
        assert client.post("/internal/worker/run-once").status_code == 401
        assert client.post("/internal/worker/run-once", headers=headers).json() == {"processed": True}
        events = client.get(f"/v1/incidents/{task_id}/events", headers=headers).json()
        assert {event["event_type"] for event in events} >= {"WORKFLOW_CREATED", "AGENT_DECISION"}
        assert client.get("/metrics", headers=headers).status_code == 200


def test_dashboard_serves_ui_and_protected_operational_views(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    headers = {"Authorization": "Bearer test-admin-token"}
    viewer_headers = {"Authorization": "Bearer test-viewer-token"}
    with TestClient(app) as client:
        assert "AgentResilience Control Plane" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/v1/dashboard/summary").status_code == 401
        assert client.get("/v1/dashboard/session", headers=viewer_headers).json() == {"role": "viewer"}
        assert client.get("/v1/dashboard/session", headers=headers).json() == {"role": "administrator"}
        created = client.post(
            "/v1/incidents",
            headers=headers,
            json={"goal": "Investigate checkout errors through the operations dashboard"},
        ).json()
        incidents = client.get("/v1/dashboard/incidents", headers=headers).json()
        assert incidents[0]["task_id"] == created["task_id"]
        summary = client.get("/v1/dashboard/summary", headers=headers).json()
        assert summary["total_incidents"] == 1
        assert "queue" in summary and "statuses" in summary
        assert client.get("/v1/dashboard/incidents", headers=viewer_headers).status_code == 200
        assert client.post(
            "/v1/dashboard/dead-letters/replay",
            headers=viewer_headers,
            json={"delivery_id": 1, "task_id": created["task_id"], "actor": "viewer", "reason": "not allowed"},
        ).status_code == 401


def test_dashboard_streams_terminal_snapshot_and_replays_dlq(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/incidents",
            headers=headers,
            json={"goal": "Exercise dashboard event streaming and dead-letter replay"},
        ).json()
        task_id = created["task_id"]
        store = app.state.store
        dead_lettered = False
        for _ in range(test_settings.max_queue_attempts):
            delivery = store.claim("failing-worker", 30)
            assert delivery
            dead_lettered = store.retry_or_dead_letter(delivery, "injected outage", 0)
        assert dead_lettered
        workflow = store.get_workflow(task_id)
        workflow.status = WorkflowStatus.DEAD_LETTERED
        store.save_workflow(workflow, workflow.version)

        stream = client.get(f"/v1/dashboard/incidents/{task_id}/stream", headers=headers)
        assert stream.status_code == 200
        assert "event: incident" in stream.text and "DEAD_LETTERED" in stream.text

        dead = client.get("/v1/dashboard/dead-letters", headers=headers).json()
        replayed = client.post(
            "/v1/dashboard/dead-letters/replay",
            headers=headers,
            json={
                "delivery_id": dead[0]["id"], "task_id": task_id,
                "actor": "on-call", "reason": "dependency recovered",
            },
        )
        assert replayed.status_code == 200
        assert replayed.json()["status"] == WorkflowStatus.QUEUED


def test_api_requires_authorization_and_resumes_after_approval(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/incidents",
            headers=headers,
            json={"goal": "Investigate checkout failures and safely restore service"},
        ).json()
        task_id = created["task_id"]
        state = created
        for _ in range(12):
            client.post("/internal/worker/run-once", headers=headers)
            state = client.get(f"/v1/incidents/{task_id}", headers=headers).json()
            if state["status"] == WorkflowStatus.WAITING_FOR_APPROVAL:
                break
        assert state["status"] == WorkflowStatus.WAITING_FOR_APPROVAL
        assert client.post(
            f"/v1/incidents/{task_id}/approve", json={"actor": "attacker"}
        ).status_code == 401
        approved = client.post(
            f"/v1/incidents/{task_id}/approve",
            headers=headers,
            json={"actor": "on-call", "reason": "evidence supports restart"},
        )
        assert approved.status_code == 200
        for _ in range(8):
            client.post("/internal/worker/run-once", headers=headers)
            state = client.get(f"/v1/incidents/{task_id}", headers=headers).json()
            if state["status"] == WorkflowStatus.COMPLETED:
                break
        assert state["status"] == WorkflowStatus.COMPLETED
        assert state["evidence"]["verify_recovery"]["error_rate_percent"] < 1


def test_aws_api_fails_closed_without_control_plane_tokens():
    with pytest.raises(ValueError, match="requires ADMIN_API_TOKEN"):
        create_app(Settings(runtime_backend="aws", admin_api_token=None, viewer_api_token=None))
