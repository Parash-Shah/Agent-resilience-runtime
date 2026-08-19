package agentresilience;

import agentresilience.model.WorkflowState;
import agentresilience.reliability.AuditLog;
import agentresilience.reliability.CheckpointStore;
import agentresilience.reliability.LoopDetector;
import agentresilience.runtime.IncidentPlan;
import agentresilience.runtime.WorkflowRuntime;
import agentresilience.tools.PermissionPolicy;
import agentresilience.tools.ToolException;
import agentresilience.tools.ToolGateway;

import java.io.IOException;
import java.nio.file.Path;
import java.util.Arrays;

public final class AgentResilienceApplication {
    private static final Path DATA = Path.of("data");

    public int run(String[] args) {
        try {
            if (args.length == 0 || Arrays.asList(args).contains("--help")) { printHelp(); return 0; }
            String command = args[0];
            String taskId = option(args, "--task", "incident-482");
            CheckpointStore checkpoints = new CheckpointStore(DATA.resolve("checkpoints"));
            if ("status".equals(command)) {
                var state = checkpoints.load(taskId);
                if (state.isEmpty()) { System.out.println("No checkpoint found for " + taskId); return 1; }
                printState(state.get());
                return 0;
            }
            if ("approve".equals(command)) {
                WorkflowState state = checkpoints.load(taskId)
                        .orElseThrow(() -> new IllegalArgumentException("Unknown task " + taskId));
                if (state.pendingApproval() == null) throw new IllegalStateException("Task is not waiting for approval");
                String approvedStep = state.pendingApproval();
                state.approvals().add(approvedStep);
                state.pendingApproval(null);
                checkpoints.save(state);
                new AuditLog(DATA.resolve("audit.jsonl")).record(taskId, "APPROVAL_GRANTED", approvedStep);
                System.out.println("Approved; resuming " + taskId);
                return execute(taskId, 0);
            }
            if ("reject".equals(command)) {
                WorkflowState state = checkpoints.load(taskId)
                        .orElseThrow(() -> new IllegalArgumentException("Unknown task " + taskId));
                if (state.pendingApproval() == null) throw new IllegalStateException("Task is not waiting for approval");
                String rejectedStep = state.pendingApproval();
                state.pendingApproval(null);
                state.status(agentresilience.model.WorkflowStatus.HUMAN_REJECTED);
                state.failureReason("Human rejected action " + rejectedStep);
                checkpoints.save(state);
                new AuditLog(DATA.resolve("audit.jsonl")).record(taskId, "APPROVAL_REJECTED", rejectedStep);
                printState(state);
                return 0;
            }
            if ("run".equals(command))
                return execute(taskId, Integer.parseInt(option(args, "--crash-after", "0")));
            throw new IllegalArgumentException("Unknown command: " + command);
        } catch (WorkflowRuntime.SimulatedCrashException crash) {
            System.err.println("CRASH: " + crash.getMessage());
            System.err.println("Run the same command without --crash-after to resume.");
            return 75;
        } catch (Exception failure) {
            System.err.println("ERROR: " + failure.getMessage());
            return 1;
        }
    }

    private int execute(String taskId, int crashAfter) throws IOException {
        WorkflowRuntime runtime = new WorkflowRuntime(
                new CheckpointStore(DATA.resolve("checkpoints")),
                new AuditLog(DATA.resolve("audit.jsonl")),
                demoTools(), new LoopDetector(4), System.out::println);
        WorkflowState state = runtime.startOrResume(taskId,
                "Investigate and recover elevated checkout-service errors",
                IncidentPlan.checkoutRecovery(), crashAfter);
        printState(state);
        return switch (state.status()) {
            case COMPLETED, WAITING_FOR_APPROVAL -> 0;
            default -> 1;
        };
    }

    private ToolGateway demoTools() {
        return new ToolGateway(new PermissionPolicy())
                .register("read_alert", context -> "checkout error rate is 14.2%")
                .register("inspect_metrics", context -> {
                    if (context.attempt() == 1) throw new ToolException("metrics endpoint timed out", true);
                    return "latency and DB waiters rose together";
                })
                .register("query_logs", context -> "connection acquisition timeout in checkout-service")
                .register("dependency_health", context -> "database healthy; checkout pool is exhausted")
                .register("restart_production_service", context -> "checkout-service restarted safely")
                .register("verify_recovery", context -> "error rate returned to 0.3%")
                .register("delete_database", context -> "should never execute");
    }

    private static String option(String[] args, String name, String fallback) {
        for (int i = 0; i < args.length - 1; i++) if (name.equals(args[i])) return args[i + 1];
        return fallback;
    }

    private static void printState(WorkflowState state) {
        String completed = state.completedSteps().stream().map(AgentResilienceApplication::jsonString)
                .collect(java.util.stream.Collectors.joining(", ", "[", "]"));
        System.out.println("{\n  \"task_id\": " + jsonString(state.taskId()) + ",\n  \"status\": " + jsonString(state.status().name())
                + ",\n  \"completed_steps\": " + completed + ",\n  \"next_step_index\": "
                + state.nextStepIndex() + ",\n  \"pending_approval\": "
                + (state.pendingApproval() == null ? "null" : jsonString(state.pendingApproval())) + ",\n  \"failure_reason\": "
                + (state.failureReason() == null ? "null" : jsonString(state.failureReason())) + "\n}");
    }

    private static String jsonString(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    private static void printHelp() {
        System.out.println("""
                AgentResilience - durable autonomous workflow demo

                java -cp out Main run [--task incident-482] [--crash-after 3]
                java -cp out Main status [--task incident-482]
                java -cp out Main approve [--task incident-482]
                java -cp out Main reject [--task incident-482]
                """);
    }
}
