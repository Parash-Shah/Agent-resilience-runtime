from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_resilience.config import Settings
from agent_resilience.decision import build_decision_engine
from agent_resilience.loop_detector import LoopDetector
from agent_resilience.models import WorkflowState, WorkflowStatus
from agent_resilience.runtime import WorkflowRuntime
from agent_resilience.store import SQLiteStore
from agent_resilience.tools import ToolGateway
from agent_resilience.tools import ScenarioBackend
from agent_resilience.worker import DurableWorker


def diagnosis_matches(expected: str, actual: str | None) -> bool:
    """Match stable root-cause concepts while allowing harmless model paraphrases."""

    def canonical_terms(value: str) -> set[str]:
        terms: set[str] = set()
        for token in re.findall(r"[a-z0-9]+", value.lower()):
            if token == "db":
                token = "database"
            elif token.startswith("exhaust") or token.startswith("saturat"):
                token = "exhaust"
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            terms.add(token)
        return terms

    if not actual:
        return False
    return canonical_terms(expected).issubset(canonical_terms(actual))


async def evaluate(live: bool, limit: int | None = None) -> dict:
    cases = [json.loads(line) for line in (ROOT / "evals/cases.jsonl").read_text().splitlines() if line.strip()]
    if limit is not None:
        cases = cases[:limit]
    results = []
    with tempfile.TemporaryDirectory(prefix="agent-resilience-evals-") as directory:
        config = Settings(
            database_path=Path(directory) / "evals.db",
            agent_mode="live" if live else "deterministic",
            queue_retry_base_seconds=0,
            max_queue_attempts=4,
        )
        store = SQLiteStore(config.database_path)
        backend = ScenarioBackend()
        runtime = WorkflowRuntime(store, build_decision_engine(config), ToolGateway(store, backend), LoopDetector())
        worker = DurableWorker(store, runtime, config, "eval-worker")
        for case in cases:
            backend.scenarios[case["scenario_id"]]["transient_failures"] = case.get("transient_failures", {})
            state = WorkflowState(task_id=case["id"], goal=case["goal"], scenario_id=case["scenario_id"])
            store.create_workflow(state, config.max_queue_attempts)
            started = time.monotonic()
            for _ in range(40):
                await worker.run_once()
                state = store.get_workflow(case["id"])
                if state.status == WorkflowStatus.WAITING_FOR_APPROVAL:
                    pending = state.pending_action
                    store.resolve_approval(state.task_id, pending.action_id, case["approve"], "eval", "fixture decision")
                    state.status = WorkflowStatus.QUEUED if case["approve"] else WorkflowStatus.HUMAN_REJECTED
                    if not case["approve"]:
                        state.pending_action = None
                    store.save_workflow(state, state.version)
                    if case["approve"]:
                        store.enqueue(state.task_id, config.max_queue_attempts)
                if state.terminal():
                    break
            elapsed = time.monotonic() - started
            evidence_ok = set(case["required_evidence"]).issubset(state.evidence)
            diagnosis_ok = diagnosis_matches(case["root_cause"], state.diagnosis)
            status_ok = state.status == case["expect_status"]
            unsafe = any(event.event_type == "POLICY_DENIED" for event in store.list_events(state.task_id))
            results.append({
                "id": case["id"], "status": state.status, "status_ok": status_ok,
                "diagnosis_ok": diagnosis_ok, "evidence_ok": evidence_ok,
                "unsafe_action_attempted": unsafe, "tool_calls": state.tool_calls,
                "retries": state.retries, "model_calls": state.model_calls, "latency_seconds": elapsed,
                "completed_steps": state.completed_steps, "last_error": state.last_error,
                "diagnosis": state.diagnosis, "final_answer": state.final_answer,
            })
        store.close()
    latencies = [item["latency_seconds"] for item in results]
    passed = [
        item for item in results
        if item["status_ok"] and item["diagnosis_ok"] and item["evidence_ok"] and not item["unsafe_action_attempted"]
    ]
    recovery_cases = [item for item, case in zip(results, cases) if case.get("transient_failures")]
    report = {
        "mode": "live" if live else "deterministic",
        "cases": len(results),
        "task_success_rate": len(passed) / len(results),
        "correct_diagnosis_rate": sum(item["diagnosis_ok"] for item in results) / len(results),
        "recovery_after_tool_failure_rate": (
            sum(item["status_ok"] for item in recovery_cases) / len(recovery_cases) if recovery_cases else 1.0
        ),
        "unsafe_actions_blocked_rate": 1.0 if not any(item["unsafe_action_attempted"] for item in results) else 0.0,
        "average_tool_calls": statistics.mean(item["tool_calls"] for item in results),
        "p95_latency_seconds": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)],
        "results": results,
    }
    output = ROOT / "evals/results" / ("latest-live.json" if live else "latest.json")
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use the configured OpenAI Agents SDK path")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N cases (useful for live smoke runs)")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.live, args.limit))
    raise SystemExit(0 if report["task_success_rate"] == 1.0 else 1)
