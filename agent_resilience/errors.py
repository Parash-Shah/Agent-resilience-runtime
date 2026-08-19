class AgentResilienceError(Exception):
    """Base class for runtime failures."""


class RetryableWorkflowError(AgentResilienceError):
    """A transient failure that should return to the durable queue."""


class PermanentWorkflowError(AgentResilienceError):
    """A non-retryable failure."""


class ConcurrentUpdateError(RetryableWorkflowError):
    """The checkpoint changed since it was loaded."""


class ApprovalRequired(AgentResilienceError):
    """Execution paused at a human approval boundary."""
