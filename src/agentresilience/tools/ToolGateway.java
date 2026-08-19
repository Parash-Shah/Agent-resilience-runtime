package agentresilience.tools;

import agentresilience.model.StepDefinition;
import agentresilience.model.WorkflowState;

import java.util.LinkedHashMap;
import java.util.Map;

public final class ToolGateway {
    private final PermissionPolicy policy;
    private final Map<String, Tool> tools = new LinkedHashMap<>();
    public ToolGateway(PermissionPolicy policy) { this.policy = policy; }
    public ToolGateway register(String name, Tool tool) { tools.put(name, tool); return this; }
    public ToolDecision decision(StepDefinition step, WorkflowState state) { return policy.evaluate(step.risk(), step.id(), state); }

    /** Cached results prevent replay; downstream adapters also receive the stable idempotency key. */
    public String execute(StepDefinition step, WorkflowState state, int attempt) throws ToolException {
        String idempotencyKey = state.taskId() + ":" + step.id();
        if (state.toolResults().containsKey(idempotencyKey)) return state.toolResults().get(idempotencyKey);
        Tool tool = tools.get(step.tool());
        if (tool == null) throw new ToolException("Unknown tool: " + step.tool(), false);
        String result = tool.execute(new ToolContext(state, step.id(), attempt, idempotencyKey));
        if (result == null || result.isBlank()) throw new ToolException("Tool returned invalid empty output", true);
        state.toolResults().put(idempotencyKey, result);
        return result;
    }
}
