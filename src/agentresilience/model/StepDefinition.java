package agentresilience.model;

public record StepDefinition(String id, String tool, RiskLevel risk, int maxAttempts) {
    public StepDefinition {
        if (id == null || id.isBlank() || tool == null || tool.isBlank())
            throw new IllegalArgumentException("Step id and tool are required");
        if (maxAttempts < 1) throw new IllegalArgumentException("maxAttempts must be positive");
    }
}
