from fastapi.testclient import TestClient

from agent_resilience.api import create_app
from agent_resilience.decision import DeterministicDecisionEngine
from agent_resilience.models import WorkflowStatus


def test_api_exposes_health_incident_and_events(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        created = client.post(
            "/v1/incidents",
            json={"goal": "Investigate why checkout-service has elevated production errors"},
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        assert client.get(f"/v1/incidents/{task_id}").status_code == 200
        assert client.post("/internal/worker/run-once").status_code == 401
        headers = {"Authorization": "Bearer test-admin-token"}
        assert client.post("/internal/worker/run-once", headers=headers).json() == {"processed": True}
        events = client.get(f"/v1/incidents/{task_id}/events").json()
        assert {event["event_type"] for event in events} >= {"WORKFLOW_CREATED", "AGENT_DECISION"}
        assert client.get("/metrics").status_code == 200


def test_api_requires_authorization_and_resumes_after_approval(test_settings):
    app = create_app(test_settings, DeterministicDecisionEngine())
    headers = {"Authorization": "Bearer test-admin-token"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/incidents",
            json={"goal": "Investigate checkout failures and safely restore service"},
        ).json()
        task_id = created["task_id"]
        state = created
        for _ in range(12):
            client.post("/internal/worker/run-once", headers=headers)
            state = client.get(f"/v1/incidents/{task_id}").json()
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
            state = client.get(f"/v1/incidents/{task_id}").json()
            if state["status"] == WorkflowStatus.COMPLETED:
                break
        assert state["status"] == WorkflowStatus.COMPLETED
        assert state["evidence"]["verify_recovery"]["error_rate_percent"] < 1
