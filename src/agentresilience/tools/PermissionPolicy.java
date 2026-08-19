package agentresilience.tools;

import agentresilience.model.RiskLevel;
import agentresilience.model.WorkflowState;

public final class PermissionPolicy {
    public ToolDecision evaluate(RiskLevel risk, String stepId, WorkflowState state) {
        if (risk == RiskLevel.BLOCKED) return ToolDecision.DENY;
        if (risk == RiskLevel.HIGH && !state.approvals().contains(stepId)) return ToolDecision.REQUIRE_APPROVAL;
        return ToolDecision.ALLOW;
    }
}
