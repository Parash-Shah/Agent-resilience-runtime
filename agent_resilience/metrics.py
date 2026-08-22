from prometheus_client import Counter, Gauge, Histogram

from .config import Settings


WORKFLOWS_CREATED = Counter("agent_workflows_created_total", "Created workflows")
WORKFLOWS_COMPLETED = Counter("agent_workflows_completed_total", "Completed workflows")
WORKFLOW_FAILURES = Counter("agent_workflow_failures_total", "Workflow failures", ["reason"])
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool calls", ["tool", "outcome"])
MODEL_CALLS = Counter("agent_model_calls_total", "Model decisions", ["outcome"])
APPROVALS = Counter("agent_approvals_total", "Approval decisions", ["decision"])
QUEUE_DELIVERIES = Counter("agent_queue_deliveries_total", "Queue deliveries", ["outcome"])
DECISION_LATENCY = Histogram("agent_decision_latency_seconds", "Agent decision latency")
QUEUE_DEPTH = Gauge("agent_queue_depth", "Queue items", ["status"])

_cloudwatch = None
_namespace = "AgentResilience"


def configure_cloudwatch_metrics(config: Settings) -> None:
    global _cloudwatch, _namespace
    _namespace = config.cloudwatch_metric_namespace
    if not config.cloudwatch_metrics_enabled:
        _cloudwatch = None
        return
    import boto3

    options = {"endpoint_url": config.aws_endpoint_url} if config.aws_endpoint_url else {}
    _cloudwatch = boto3.client("cloudwatch", region_name=config.aws_region, **options)


def emit_metric(name: str, value: float = 1.0, **dimensions: str) -> None:
    """Best-effort CloudWatch publication; telemetry must never break workflow recovery."""
    if _cloudwatch is None:
        return
    try:
        _cloudwatch.put_metric_data(
            Namespace=_namespace,
            MetricData=[{
                "MetricName": name,
                "Value": value,
                "Unit": "Seconds" if name == "DecisionLatency" else "Count",
                "Dimensions": [{"Name": key, "Value": str(item)} for key, item in dimensions.items()],
            }],
        )
    except Exception:
        return
