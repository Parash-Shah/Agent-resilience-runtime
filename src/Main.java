import agentresilience.AgentResilienceApplication;

/** IntelliJ entry point for the AgentResilience runtime. */
public final class Main {
    private Main() {}

    public static void main(String[] args) {
        int exitCode = new AgentResilienceApplication().run(args);
        if (exitCode != 0) System.exit(exitCode);
    }
}
