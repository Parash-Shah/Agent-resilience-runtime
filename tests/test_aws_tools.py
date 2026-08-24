from __future__ import annotations

from botocore.stub import Stubber
import pytest

from agent_resilience.aws_tools import AWSOperationsBackend
from agent_resilience.config import Settings
from agent_resilience.errors import RetryableWorkflowError
from agent_resilience.models import ToolName, WorkflowState


def test_aws_ecs_restart_uses_bounded_force_deployment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    config = Settings(
        aws_region="us-east-1",
        ecs_cluster="production-cluster",
        ecs_service_prefix="managed-",
    )
    backend = AWSOperationsBackend(config)
    with Stubber(backend.ecs) as stubber:
        stubber.add_response(
            "update_service",
            {"service": {"deployments": [{"id": "ecs-svc/123", "status": "PRIMARY"}]}},
            {"cluster": "production-cluster", "service": "managed-checkout-service", "forceNewDeployment": True},
        )
        result = backend.execute(
            WorkflowState(task_id="tool-test", goal="Restart only an approved bounded ECS service"),
            ToolName.RESTART_SERVICE,
            {"service": "checkout-service", "environment": "production"},
            1,
        )
    assert result["restarted"] is True
    assert result["service"] == "managed-checkout-service"


def test_recovery_waits_for_stable_ecs_and_healthy_cloudwatch(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    backend = AWSOperationsBackend(Settings(aws_region="us-east-1"))
    arguments = {"service": "checkout-service", "environment": "production"}
    monkeypatch.setattr(backend, "_service_health", lambda _: {
        "desired_count": 1,
        "running_count": 0,
        "pending_count": 1,
        "deployments": [{"rollout_state": "IN_PROGRESS"}],
    })
    with pytest.raises(RetryableWorkflowError, match="not stable"):
        backend._verify_recovery(arguments)

    monkeypatch.setattr(backend, "_service_health", lambda _: {
        "desired_count": 1,
        "running_count": 1,
        "pending_count": 0,
        "deployments": [{"rollout_state": "COMPLETED"}],
    })
    monkeypatch.setattr(backend, "_inspect_metrics", lambda _: {"error_rate_percent": 0.2})
    assert backend._verify_recovery(arguments)["recovered"] is True
