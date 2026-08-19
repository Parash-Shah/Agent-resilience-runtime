# AgentResilience

A fault-tolerant runtime for reliable autonomous agents. This first milestone runs a realistic incident workflow while keeping tool execution behind durable checkpoints and permission boundaries.

## Reliability behavior included

- Atomic file-backed checkpoints and resume from the last completed step
- Stable idempotency keys passed to downstream adapters, plus cached tool results to prevent replay after checkpointing
- Bounded retry handling for transient tool failures
- Risk policy: low/medium allowed, high requires approval, blocked is denied
- Durable JSONL audit events
- Repeated tool-pattern loop detection
- Invalid/empty tool-output rejection
- A controlled crash mode that demonstrates checkpoint recovery

The tools are deterministic local adapters for now. `CheckpointStore` and `ToolGateway` are explicit seams for DynamoDB/SQS and real monitoring/AWS adapters in later milestones.

## Run

Use JDK 21 or newer from the project root:

```powershell
javac -d out (Get-ChildItem -Recurse src -Filter *.java).FullName
java -cp out Main run --task incident-482 --crash-after 3
java -cp out Main status --task incident-482
java -cp out Main run --task incident-482
java -cp out Main approve --task incident-482
# Or deny the proposed production action:
java -cp out Main reject --task incident-482
```

The first command checkpoints three completed steps and simulates a process crash. The next `run` resumes at dependency health. The production restart pauses at the approval gate; `approve` records approval and completes recovery. `reject` records the human decision and terminates the workflow without running the tool.

Runtime state is written to `data/checkpoints/`; append-only events are in `data/audit.jsonl`.

## Test

```powershell
javac -d out (Get-ChildItem -Recurse src,test -Filter *.java).FullName
java -cp out AgentResilienceTests
```
