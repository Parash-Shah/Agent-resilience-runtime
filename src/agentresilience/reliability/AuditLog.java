package agentresilience.reliability;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;

public final class AuditLog {
    private final Path file;
    public AuditLog(Path file) { this.file = file; }
    public synchronized void record(String taskId, String event, String detail) throws IOException {
        Files.createDirectories(file.getParent());
        String line = "{\"time\":\"" + Instant.now() + "\",\"task_id\":\"" + escape(taskId)
                + "\",\"event\":\"" + escape(event) + "\",\"detail\":\"" + escape(detail) + "\"}\n";
        Files.writeString(file, line, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
    }
    private static String escape(String value) { return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n"); }
}
