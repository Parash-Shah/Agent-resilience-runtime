package agentresilience.tools;

public final class ToolException extends Exception {
    private final boolean retryable;
    public ToolException(String message, boolean retryable) { super(message); this.retryable = retryable; }
    public boolean retryable() { return retryable; }
}
