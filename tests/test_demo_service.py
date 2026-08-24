from fastapi.testclient import TestClient

from agent_resilience.demo_service import CheckoutDemo, create_demo_app


def test_demo_starts_healthy_and_exposes_checkout():
    demo = CheckoutDemo(fail_after_seconds=60)
    with TestClient(create_demo_app(demo)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/checkout")
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"


def test_restart_recovery_model_is_process_local():
    failed = CheckoutDemo(fail_after_seconds=0.001)
    failed.started_at -= 1
    with TestClient(create_demo_app(failed)) as client:
        assert client.get("/health").status_code == 503
        assert client.get("/checkout").json()["error"] == "database connection pool exhausted"

    replacement = CheckoutDemo(fail_after_seconds=60)
    with TestClient(create_demo_app(replacement)) as client:
        assert client.get("/health").status_code == 200
