# AgentResilience

**A fault-tolerant runtime for reliable autonomous AI agents.**

AgentResilience investigates production incidents with an OpenAI Agents SDK orchestrator while keeping execution recoverable, idempotent, observable, and bounded by policy. A model can decide what evidence it needs; it cannot directly use infrastructure credentials or bypass the tool gateway.

![Agent interaction architecture](docs/agent-interactions.png)

## What is implemented

- **Resumable execution:** every agent decision and tool result is checkpointed in SQLite with optimistic version checks. Each queue delivery performs one bounded action, so a replacement worker resumes at the next incomplete action.
- **Durable queue:** atomic claims, worker leases, expired-lease recovery, exponential retries, and dead-lettering after a bounded attempt count.
- **Idempotent tools:** stable action IDs and persisted results prevent duplicate infrastructure calls after crashes or redelivery.
- **Agentic orchestration:** a typed Agents SDK orchestrator chooses the next action and can consult log-analysis, cloud-state, and remediation specialist agents.
- **Safety:** Pydantic argument validation, explicit risk policy, blocked operations, authenticated approval/rejection endpoints, and no infrastructure credentials in the model context.
- **Human approval:** production restart requests pause durably and continue only after an authorized approval. Rejection is a terminal, audited outcome.
- **Loop protection:** repeated tool cycles and repeated no-progress state fingerprints stop the workflow.
- **Observability:** append-only audit events, OpenAI agent traces, Prometheus metrics, an optional OpenTelemetry exporter, and a provisioned Grafana dashboard.
- **Evaluation:** an offline incident suite scores task outcome, diagnosis, evidence, unsafe attempts, tool calls, retries, and P95 latency without API cost. A `--live` mode evaluates the actual model path.
- **Deployment assets:** a non-root container, separate API and worker services, Prometheus/Grafana Compose stack, GitHub Actions CI, and Terraform for encrypted SQS/DLQ, DynamoDB, and CloudWatch primitives.

The runnable persistence adapter is SQLite for a self-contained demonstration. The Terraform module provisions the AWS reliability primitives; connecting them requires an AWS store/queue adapter at the existing `SQLiteStore` boundary.

## Failure coverage

| Failure | Runtime response |
|---|---|
| LLM timeout or downstream tool timeout | Retry with bounded exponential backoff |
| Invalid typed model/tool output | Reject and retry or fail permanently by category |
| Unauthorized or destructive action | Policy denial before execution |
| Process crash | Queue lease expires; another worker reloads the checkpoint |
| Duplicate delivery/tool request | Idempotency result is replayed, not the side effect |
| Repeated tool loop/no progress | Workflow stops as `LOOP_STOPPED` |
| Downstream unavailable repeatedly | Delivery moves to the DLQ; workflow becomes `DEAD_LETTERED` |
| Oversized accumulated evidence | Prompt is compacted to a configured evidence bound |
| Human rejection | Workflow becomes `HUMAN_REJECTED`; tool never runs |

![Crash recovery sequence](docs/agent-sequence.png)

## Local setup

Requires Python 3.11+.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
```

Edit `.env.local` and add the project-scoped OpenAI key you created. The file is ignored by Git. For a zero-cost deterministic run, set `AGENT_MODE=deterministic`; no OpenAI request is made.

Run API and worker in separate terminals:

```powershell
.venv\Scripts\agent-resilience-api.exe
.venv\Scripts\agent-resilience-worker.exe
```

API docs are at `http://localhost:8000/docs`.

## Exercise an incident

```powershell
$incident = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/incidents `
  -ContentType application/json `
  -Body '{"goal":"Investigate why checkout-service has elevated production errors"}'

Invoke-RestMethod http://localhost:8000/v1/incidents/$($incident.task_id)

$headers = @{ Authorization = "Bearer $env:ADMIN_API_TOKEN" }
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v1/incidents/$($incident.task_id)/approve" `
  -Headers $headers -ContentType application/json -Body '{"actor":"on-call","reason":"evidence supports restart"}'
```

The incident proceeds through alert, metrics, logs, and dependency evidence; pauses before the high-risk production restart; then executes and verifies recovery after approval. Read its audit trail at `/v1/incidents/{task_id}/events`.

To demonstrate crash recovery, stop the worker after any `TOOL_COMPLETED` event and restart it. The expired/incomplete delivery is recovered and execution continues from the persisted state; completed tools are not run again.

## Test and evaluate

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe evals/run_local.py
.venv\Scripts\python.exe evals/run_local.py --live
```

Evaluation output is written to `evals/results/latest.json` and intentionally ignored by Git so published numbers must come from a real run.

## Containers and monitoring

Docker Compose reads `.env`, not `.env.local`. Copy the example and set secure values before starting it:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

- API and Swagger: `http://localhost:8000/docs`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

## Repository map

```text
agent_resilience/   API, agent orchestration, runtime, store, tools, safety
tests/              recovery, queue, policy, loop, and API tests
evals/              repeatable incident evaluation suite
fixtures/           deterministic incident/tool responses
ops/                Prometheus and Grafana configuration
infra/terraform/    AWS durable messaging, state, and monitoring primitives
docs/               system prompt and generated architecture diagrams
src/, test/         original Java proof of concept retained for history
```

## Security notes

- Never commit `.env` or `.env.local`.
- Set a strong `ADMIN_API_TOKEN` in any shared environment.
- Replace the fixture backend with narrowly scoped service identities; never expose AWS credentials to agent prompts.
- Terminate TLS and add your organization identity layer in front of the API before production use.

## OpenAI implementation references

The integration follows the official [Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart), [orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [tracing](https://developers.openai.com/api/docs/guides/agents/integrations-observability), and [agent evals](https://developers.openai.com/api/docs/guides/agent-evals) guidance.

## License

MIT
