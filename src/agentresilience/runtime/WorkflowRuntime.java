package agentresilience.runtime;

import agentresilience.model.StepDefinition;
import agentresilience.model.WorkflowState;
import agentresilience.model.WorkflowStatus;
import agentresilience.reliability.AuditLog;
import agentresilience.reliability.CheckpointStore;
import agentresilience.reliability.LoopDetector;
import agentresilience.tools.ToolDecision;
import agentresilience.tools.ToolException;
import agentresilience.tools.ToolGateway;

import java.io.IOException;
import java.util.List;
import java.util.function.Consumer;

public final class WorkflowRuntime {
    private final CheckpointStore checkpoints;
    private final AuditLog audit;
    private final ToolGateway tools;
    private final LoopDetector loopDetector;
    private final Consumer<String> output;

    public WorkflowRuntime(CheckpointStore checkpoints, AuditLog audit, ToolGateway tools,
                           LoopDetector loopDetector, Consumer<String> output) {
        this.checkpoints = checkpoints;
        this.audit = audit;
        this.tools = tools;
        this.loopDetector = loopDetector;
        this.output = output;
    }

    public WorkflowState startOrResume(String taskId, String goal, List<StepDefinition> plan, int crashAfter) throws IOException {
        WorkflowState state = checkpoints.load(taskId).orElseGet(() -> new WorkflowState(taskId, goal));
        if (state.status() == WorkflowStatus.COMPLETED) {
            output.accept("Task " + taskId + " is already complete; no tools were repeated.");
            return state;
        }
        if (state.status() == WorkflowStatus.HUMAN_REJECTED) {
            output.accept("Task " + taskId + " was rejected by a human; no tools were executed.");
            return state;
        }
        if (state.nextStepIndex() > 0) output.accept("RESUMING FROM STEP " + (state.nextStepIndex() + 1));
        state.status(WorkflowStatus.IN_PROGRESS);
        state.pendingApproval(null);
        checkpoints.save(state);
        int completedThisRun = 0;

        while (state.nextStepIndex() < plan.size()) {
            StepDefinition step = plan.get(state.nextStepIndex());
            ToolDecision decision = tools.decision(step, state);
            if (decision == ToolDecision.DENY) return stop(state, WorkflowStatus.FAILED, "Policy denied tool " + step.tool());
            if (decision == ToolDecision.REQUIRE_APPROVAL) {
                state.status(WorkflowStatus.WAITING_FOR_APPROVAL);
                state.pendingApproval(step.id());
                checkpoints.save(state);
                audit.record(taskId, "APPROVAL_REQUIRED", step.id());
                output.accept("APPROVAL REQUIRED for " + step.id() + " (HIGH risk)");
                return state;
            }

            boolean succeeded = false;
            while (!succeeded) {
                int attempt = state.attempts().merge(step.id(), 1, Integer::sum);
                state.toolHistory().add(step.tool());
                audit.record(taskId, "TOOL_STARTED", step.tool() + " attempt=" + attempt);
                if (loopDetector.isLoop(state.toolHistory()))
                    return stop(state, WorkflowStatus.STOPPED_LOOP, "Repeated tool pattern detected");
                try {
                    String result = tools.execute(step, state, attempt);
                    state.completedSteps().add(step.id());
                    state.nextStepIndex(state.nextStepIndex() + 1);
                    state.failureReason(null);
                    checkpoints.save(state);
                    audit.record(taskId, "STEP_COMPLETED", step.id() + ": " + result);
                    output.accept("[ok] " + step.id() + " -> " + result);
                    succeeded = true;
                    completedThisRun++;
                } catch (ToolException failure) {
                    audit.record(taskId, "TOOL_FAILED", step.tool() + ": " + failure.getMessage());
                    state.failureReason(failure.getMessage());
                    checkpoints.save(state);
                    if (!failure.retryable() || attempt >= step.maxAttempts())
                        return stop(state, WorkflowStatus.FAILED, failure.getMessage());
                    output.accept("[retry] " + step.id() + " failed: " + failure.getMessage());
                }
            }
            if (crashAfter > 0 && completedThisRun >= crashAfter) {
                audit.record(taskId, "PROCESS_CRASH_SIMULATED", "checkpoint is durable");
                throw new SimulatedCrashException("Simulated process crash after durable checkpoint");
            }
        }
        state.status(WorkflowStatus.COMPLETED);
        checkpoints.save(state);
        audit.record(taskId, "WORKFLOW_COMPLETED", state.goal());
        output.accept("WORKFLOW COMPLETED: " + taskId);
        return state;
    }

    private WorkflowState stop(WorkflowState state, WorkflowStatus status, String reason) throws IOException {
        state.status(status);
        state.failureReason(reason);
        checkpoints.save(state);
        audit.record(state.taskId(), status.name(), reason);
        output.accept(status + ": " + reason);
        return state;
    }

    public static final class SimulatedCrashException extends RuntimeException {
        public SimulatedCrashException(String message) { super(message); }
    }
}
