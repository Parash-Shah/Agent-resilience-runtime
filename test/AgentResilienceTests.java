import agentresilience.model.RiskLevel;
import agentresilience.model.StepDefinition;
import agentresilience.model.WorkflowState;
import agentresilience.reliability.CheckpointStore;
import agentresilience.reliability.LoopDetector;
import agentresilience.tools.PermissionPolicy;
import agentresilience.tools.ToolDecision;
import agentresilience.tools.ToolGateway;

import java.nio.file.Files;
import java.util.List;

public final class AgentResilienceTests {
    public static void main(String[] args) throws Exception {
        loopDetectorStopsRepeatedPatterns();
        permissionPolicyRequiresApprovalAndBlocksForbiddenActions();
        checkpointRoundTripPreservesRecoveryState();
        toolGatewaySuppliesStableIdempotencyKey();
        System.out.println("All AgentResilience tests passed.");
    }

    private static void loopDetectorStopsRepeatedPatterns() {
        LoopDetector detector = new LoopDetector(4);
        check(detector.isLoop(List.of("logs", "metrics", "logs", "metrics", "logs", "metrics", "logs", "metrics")), "loop not detected");
        check(!detector.isLoop(List.of("alert", "metrics", "logs", "health")), "false positive loop");
    }

    private static void permissionPolicyRequiresApprovalAndBlocksForbiddenActions() {
        WorkflowState state = new WorkflowState("test-1", "test");
        PermissionPolicy policy = new PermissionPolicy();
        check(policy.evaluate(RiskLevel.HIGH, "restart", state) == ToolDecision.REQUIRE_APPROVAL, "approval not required");
        state.approvals().add("restart");
        check(policy.evaluate(RiskLevel.HIGH, "restart", state) == ToolDecision.ALLOW, "approval not honored");
        check(policy.evaluate(RiskLevel.BLOCKED, "delete", state) == ToolDecision.DENY, "blocked action allowed");
    }

    private static void checkpointRoundTripPreservesRecoveryState() throws Exception {
        var directory = Files.createTempDirectory("agent-resilience-test");
        CheckpointStore store = new CheckpointStore(directory);
        WorkflowState original = new WorkflowState("incident-test", "recover service");
        original.completedSteps().add("read_alert");
        original.nextStepIndex(1);
        original.toolResults().put("incident-test:read_alert", "alert found");
        store.save(original);
        WorkflowState loaded = store.load("incident-test").orElseThrow();
        check(loaded.nextStepIndex() == 1, "next step lost");
        check(loaded.completedSteps().contains("read_alert"), "completed step lost");
        check("alert found".equals(loaded.toolResults().get("incident-test:read_alert")), "idempotency result lost");
    }

    private static void toolGatewaySuppliesStableIdempotencyKey() throws Exception {
        WorkflowState state = new WorkflowState("incident-key", "test key");
        int[] executions = {0};
        ToolGateway gateway = new ToolGateway(new PermissionPolicy()).register("restart", context -> {
            executions[0]++;
            check("incident-key:restart_service".equals(context.idempotencyKey()), "wrong idempotency key");
            return "done";
        });
        StepDefinition step = new StepDefinition("restart_service", "restart", RiskLevel.MEDIUM, 1);
        gateway.execute(step, state, 1);
        gateway.execute(step, state, 2);
        check(executions[0] == 1, "cached tool result was replayed");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
