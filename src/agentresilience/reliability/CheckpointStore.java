package agentresilience.reliability;

import agentresilience.model.WorkflowState;
import agentresilience.model.WorkflowStatus;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.Base64;
import java.util.Optional;
import java.util.Properties;

/** Atomic file-backed checkpoints; replaceable by DynamoDB without changing the runtime. */
public final class CheckpointStore {
    private final Path directory;
    public CheckpointStore(Path directory) { this.directory = directory; }

    public Optional<WorkflowState> load(String taskId) throws IOException {
        Path file = checkpointPath(taskId);
        if (!Files.exists(file)) return Optional.empty();
        Properties properties = new Properties();
        try (InputStream input = Files.newInputStream(file)) { properties.load(input); }
        WorkflowState state = new WorkflowState(properties.getProperty("taskId"), decode(properties.getProperty("goal")));
        state.status(WorkflowStatus.valueOf(properties.getProperty("status")));
        state.nextStepIndex(Integer.parseInt(properties.getProperty("nextStepIndex", "0")));
        state.pendingApproval(emptyToNull(properties.getProperty("pendingApproval", "")));
        state.failureReason(emptyToNull(decode(properties.getProperty("failureReason", ""))));
        split(properties.getProperty("completed", "")).forEach(state.completedSteps()::add);
        split(properties.getProperty("approvals", "")).forEach(state.approvals()::add);
        split(properties.getProperty("history", "")).forEach(state.toolHistory()::add);
        for (String name : properties.stringPropertyNames()) {
            if (name.startsWith("attempt.")) state.attempts().put(name.substring(8), Integer.parseInt(properties.getProperty(name)));
            if (name.startsWith("result.")) state.toolResults().put(name.substring(7), decode(properties.getProperty(name)));
        }
        state.updatedAt(Instant.parse(properties.getProperty("updatedAt")));
        return Optional.of(state);
    }

    public void save(WorkflowState state) throws IOException {
        Files.createDirectories(directory);
        Properties properties = new Properties();
        properties.setProperty("taskId", state.taskId());
        properties.setProperty("goal", encode(state.goal()));
        properties.setProperty("status", state.status().name());
        properties.setProperty("nextStepIndex", Integer.toString(state.nextStepIndex()));
        properties.setProperty("pendingApproval", nullToEmpty(state.pendingApproval()));
        properties.setProperty("failureReason", encode(nullToEmpty(state.failureReason())));
        properties.setProperty("completed", String.join(",", state.completedSteps()));
        properties.setProperty("approvals", String.join(",", state.approvals()));
        properties.setProperty("history", String.join(",", state.toolHistory()));
        properties.setProperty("updatedAt", state.updatedAt().toString());
        state.attempts().forEach((key, value) -> properties.setProperty("attempt." + key, value.toString()));
        state.toolResults().forEach((key, value) -> properties.setProperty("result." + key, encode(value)));
        Path target = checkpointPath(state.taskId());
        Path temporary = Files.createTempFile(directory, state.taskId() + "-", ".tmp");
        try (OutputStream output = Files.newOutputStream(temporary)) { properties.store(output, "AgentResilience checkpoint"); }
        try {
            Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException ignored) {
            Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private Path checkpointPath(String taskId) {
        if (!taskId.matches("[a-zA-Z0-9_-]+")) throw new IllegalArgumentException("Invalid task id");
        return directory.resolve(taskId + ".state");
    }
    private static java.util.List<String> split(String value) { return value.isBlank() ? java.util.List.of() : java.util.List.of(value.split(",")); }
    private static String encode(String value) { return Base64.getUrlEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8)); }
    private static String decode(String value) { return new String(Base64.getUrlDecoder().decode(value), StandardCharsets.UTF_8); }
    private static String nullToEmpty(String value) { return value == null ? "" : value; }
    private static String emptyToNull(String value) { return value == null || value.isEmpty() ? null : value; }
}
