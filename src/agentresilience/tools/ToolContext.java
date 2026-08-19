package agentresilience.tools;

import agentresilience.model.WorkflowState;

/** The adapter must pass idempotencyKey through to any side-effecting downstream API. */
public record ToolContext(WorkflowState state, String stepId, int attempt, String idempotencyKey) {}
