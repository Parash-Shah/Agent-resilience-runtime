from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings
from .errors import PermanentWorkflowError, RetryableWorkflowError
from .models import ToolName, WorkflowState


class AWSOperationsBackend:
    """Least-authority adapter for CloudWatch evidence and controlled ECS deployment refreshes."""

    def __init__(self, config: Settings):
        session = boto3.session.Session(region_name=config.aws_region)
        options = {"endpoint_url": config.aws_endpoint_url} if config.aws_endpoint_url else {}
        self.cloudwatch = session.client("cloudwatch", **options)
        self.logs = session.client("logs", **options)
        self.ecs = session.client("ecs", **options)
        self.config = config

    def execute(self, state: WorkflowState, tool: ToolName, arguments: dict[str, Any], attempt: int) -> dict[str, Any]:
        del state, attempt
        try:
            if tool == ToolName.READ_ALERT:
                return self._read_alert(arguments)
            if tool == ToolName.INSPECT_METRICS:
                return self._inspect_metrics(arguments)
            if tool == ToolName.QUERY_LOGS:
                return self._query_logs(arguments)
            if tool == ToolName.DEPENDENCY_HEALTH:
                return self._service_health(arguments)
            if tool == ToolName.RESTART_SERVICE:
                return self._restart_service(arguments)
            if tool == ToolName.VERIFY_RECOVERY:
                return self._verify_recovery(arguments)
            raise PermanentWorkflowError(f"AWS backend does not implement {tool.value}")
        except PermanentWorkflowError:
            raise
        except (ClientError, BotoCoreError) as error:
            raise self._classify(error, tool) from error

    def _read_alert(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = arguments["service"]
        response = self.cloudwatch.describe_alarms(AlarmNamePrefix=service, MaxRecords=25)
        alarms = [
            {
                "name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "reason": alarm.get("StateReason", ""),
                "updated_at": alarm.get("StateUpdatedTimestamp", datetime.now(UTC)).isoformat(),
            }
            for alarm in response.get("MetricAlarms", [])
        ]
        return {"service": service, "alarms": alarms, "in_alarm": any(a["state"] == "ALARM" for a in alarms)}

    def _inspect_metrics(self, arguments: dict[str, Any]) -> dict[str, Any]:
        end = datetime.now(UTC)
        start = end - timedelta(minutes=15)
        dimensions = [
            {"Name": "ServiceName", "Value": arguments["service"]},
            {"Name": "Environment", "Value": arguments["environment"]},
        ]
        values: dict[str, Any] = {}
        for metric, statistic in (("ErrorRate", "Average"), ("Latency", "p95")):
            kwargs: dict[str, Any] = {
                "Namespace": self.config.service_metric_namespace,
                "MetricName": metric,
                "Dimensions": dimensions,
                "StartTime": start,
                "EndTime": end,
                "Period": 60,
            }
            if statistic.startswith("p"):
                kwargs["ExtendedStatistics"] = [statistic]
            else:
                kwargs["Statistics"] = [statistic]
            points = self.cloudwatch.get_metric_statistics(**kwargs).get("Datapoints", [])
            latest = max(points, key=lambda point: point["Timestamp"]) if points else None
            values[metric] = None if latest is None else float(latest.get(statistic, latest.get("Average", 0)))
        return {
            "service": arguments["service"],
            "environment": arguments["environment"],
            "window_minutes": 15,
            "error_rate_percent": values["ErrorRate"],
            "p95_latency_ms": values["Latency"],
        }

    def _query_logs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        group = f"{self.config.cloudwatch_log_group_prefix.rstrip('/')}/{arguments['service']}"
        response = self.logs.filter_log_events(
            logGroupName=group,
            startTime=int((datetime.now(UTC) - timedelta(minutes=15)).timestamp() * 1000),
            filterPattern="?ERROR ?Exception ?timeout ?503",
            limit=50,
        )
        events = [
            {"timestamp": event["timestamp"], "message": event["message"][:2_000]}
            for event in response.get("events", [])
        ]
        return {"log_group": group, "matches": len(events), "events": events}

    def _service_health(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = self._service_name(arguments["service"])
        response = self.ecs.describe_services(cluster=self.config.ecs_cluster, services=[service])
        failures = response.get("failures", [])
        if failures:
            raise PermanentWorkflowError(f"ECS service lookup failed: {failures[0].get('reason', 'unknown')}")
        services = response.get("services", [])
        if not services:
            raise PermanentWorkflowError(f"ECS service not found: {service}")
        item = services[0]
        deployments = item.get("deployments", [])
        return {
            "cluster": self.config.ecs_cluster,
            "service": service,
            "status": item.get("status"),
            "desired_count": item.get("desiredCount", 0),
            "running_count": item.get("runningCount", 0),
            "pending_count": item.get("pendingCount", 0),
            "deployments": [
                {"id": d.get("id"), "status": d.get("status"), "rollout_state": d.get("rolloutState")}
                for d in deployments[:5]
            ],
        }

    def _restart_service(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = self._service_name(arguments["service"])
        response = self.ecs.update_service(
            cluster=self.config.ecs_cluster,
            service=service,
            forceNewDeployment=True,
        )
        deployment = response["service"].get("deployments", [{}])[0]
        return {
            "cluster": self.config.ecs_cluster,
            "service": service,
            "environment": arguments["environment"],
            "deployment_id": deployment.get("id"),
            "deployment_status": deployment.get("status"),
            "restarted": True,
        }

    def _verify_recovery(self, arguments: dict[str, Any]) -> dict[str, Any]:
        service = self._service_health(arguments)
        deployments = service["deployments"]
        rollout_complete = (
            service["running_count"] >= service["desired_count"]
            and service["pending_count"] == 0
            and len(deployments) == 1
            and deployments[0].get("rollout_state") in {None, "COMPLETED"}
        )
        if not rollout_complete:
            raise RetryableWorkflowError("ECS replacement deployment is not stable yet")
        metrics = self._inspect_metrics(arguments)
        error_rate = metrics.get("error_rate_percent")
        if error_rate is None or float(error_rate) >= 1.0:
            raise RetryableWorkflowError("healthy post-restart CloudWatch evidence is not available yet")
        return {"service": service, "metrics": metrics, "recovered": True}

    def _service_name(self, requested: str) -> str:
        return f"{self.config.ecs_service_prefix}{requested}"

    @staticmethod
    def _classify(error: Exception, tool: ToolName) -> Exception:
        if isinstance(error, ClientError):
            code = error.response.get("Error", {}).get("Code", "Unknown")
            retryable = code.startswith("Throttl") or code in {
                "InternalError", "InternalFailure", "RequestLimitExceeded",
                "ServiceUnavailable", "ServiceUnavailableException",
            }
            kind = RetryableWorkflowError if retryable else PermanentWorkflowError
            return kind(f"AWS tool {tool.value} failed ({code})")
        return RetryableWorkflowError(f"AWS tool {tool.value} transport failure ({type(error).__name__})")
