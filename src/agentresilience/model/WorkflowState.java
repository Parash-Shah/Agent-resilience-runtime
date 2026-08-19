package agentresilience.model;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class WorkflowState {
    private final String taskId;
    private final String goal;
    private WorkflowStatus status = WorkflowStatus.IN_PROGRESS;
    private int nextStepIndex;
    private final Set<String> completedSteps = new LinkedHashSet<>();
    private final Set<String> approvals = new LinkedHashSet<>();
    private final Map<String, Integer> attempts = new LinkedHashMap<>();
    private final Map<String, String> toolResults = new LinkedHashMap<>();
    private final List<String> toolHistory = new ArrayList<>();
    private String pendingApproval;
    private String failureReason;
    private Instant updatedAt = Instant.now();

    public WorkflowState(String taskId, String goal) { this.taskId = taskId; this.goal = goal; }
    public String taskId() { return taskId; }
    public String goal() { return goal; }
    public WorkflowStatus status() { return status; }
    public int nextStepIndex() { return nextStepIndex; }
    public Set<String> completedSteps() { return completedSteps; }
    public Set<String> approvals() { return approvals; }
    public Map<String, Integer> attempts() { return attempts; }
    public Map<String, String> toolResults() { return toolResults; }
    public List<String> toolHistory() { return toolHistory; }
    public String pendingApproval() { return pendingApproval; }
    public String failureReason() { return failureReason; }
    public Instant updatedAt() { return updatedAt; }
    public void status(WorkflowStatus value) { status = value; touch(); }
    public void nextStepIndex(int value) { nextStepIndex = value; touch(); }
    public void pendingApproval(String value) { pendingApproval = value; touch(); }
    public void failureReason(String value) { failureReason = value; touch(); }
    public void updatedAt(Instant value) { updatedAt = value; }
    public void touch() { updatedAt = Instant.now(); }
}
