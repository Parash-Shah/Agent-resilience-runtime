package agentresilience.runtime;

import agentresilience.model.RiskLevel;
import agentresilience.model.StepDefinition;

import java.util.List;

public final class IncidentPlan {
    private IncidentPlan() {}
    public static List<StepDefinition> checkoutRecovery() {
        return List.of(
                new StepDefinition("read_alert", "read_alert", RiskLevel.LOW, 2),
                new StepDefinition("inspect_metrics", "inspect_metrics", RiskLevel.LOW, 3),
                new StepDefinition("query_logs", "query_logs", RiskLevel.LOW, 3),
                new StepDefinition("dependency_health", "dependency_health", RiskLevel.LOW, 3),
                new StepDefinition("restart_checkout", "restart_production_service", RiskLevel.HIGH, 1),
                new StepDefinition("verify_recovery", "verify_recovery", RiskLevel.LOW, 3));
    }
}
