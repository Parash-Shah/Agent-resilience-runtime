package agentresilience.tools;

@FunctionalInterface
public interface Tool { String execute(ToolContext context) throws ToolException; }
