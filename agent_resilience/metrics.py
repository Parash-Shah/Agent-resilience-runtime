from prometheus_client import Counter, Gauge, Histogram


WORKFLOWS_CREATED = Counter("agent_workflows_created_total", "Created workflows")
WORKFLOWS_COMPLETED = Counter("agent_workflows_completed_total", "Completed workflows")
WORKFLOW_FAILURES = Counter("agent_workflow_failures_total", "Workflow failures", ["reason"])
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool calls", ["tool", "outcome"])
MODEL_CALLS = Counter("agent_model_calls_total", "Model decisions", ["outcome"])
APPROVALS = Counter("agent_approvals_total", "Approval decisions", ["decision"])
QUEUE_DELIVERIES = Counter("agent_queue_deliveries_total", "Queue deliveries", ["outcome"])
DECISION_LATENCY = Histogram("agent_decision_latency_seconds", "Agent decision latency")
QUEUE_DEPTH = Gauge("agent_queue_depth", "Queue items", ["status"])
