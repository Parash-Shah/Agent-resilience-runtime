# Incident orchestrator prompt

The runtime prompt is defined in `agent_resilience/decision.py` as `ORCHESTRATOR_INSTRUCTIONS`.

The orchestrator receives a bounded snapshot containing the goal, completed steps, recent tool history,
collected evidence, available tools, and required argument defaults. It returns exactly one typed decision:

- `use_tool`: request one bounded capability through the policy-enforcing gateway;
- `complete`: provide a diagnosis, remediation, and final recovery statement;
- `fail`: terminate when the evidence cannot support safe progress.

The model never receives infrastructure credentials. Production side effects pause in the runtime approval
state before the tool adapter can execute.
