from __future__ import annotations

from botocore.stub import Stubber

from agent_resilience.aws_tools import AWSOperationsBackend
from agent_resilience.config import Settings
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
