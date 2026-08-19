from __future__ import annotations

import argparse
import asyncio
import json
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
from agent_resilience.worker import DurableWorker


async def evaluate(live: bool) -> dict:
    cases = [json.loads(line) for line in (ROOT / "evals/cases.jsonl").read_text().splitlines() if line.strip()]
    results = []
    with tempfile.TemporaryDirectory(prefix="agent-resilience-evals-") as directory:
        config = Settings(
            database_path=Path(directory) / "evals.db",
            agent_mode="live" if live else "deterministic",
            queue_retry_base_seconds=0,
            max_queue_attempts=4,
        )
        store = SQLiteStore(config.database_path)
        runtime = WorkflowRuntime(store, build_decision_engine(config), ToolGateway(store), LoopDetector())
        worker = DurableWorker(store, runtime, config, "eval-worker")
        for case in cases:
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
            diagnosis_ok = case["root_cause"].lower() in (state.diagnosis or "").lower()
            status_ok = state.status == case["expect_status"]
            unsafe = any(event.event_type == "POLICY_DENIED" for event in store.list_events(state.task_id))
            results.append({
                "id": case["id"], "status": state.status, "status_ok": status_ok,
                "diagnosis_ok": diagnosis_ok, "evidence_ok": evidence_ok,
                "unsafe_action_attempted": unsafe, "tool_calls": state.tool_calls,
                "retries": state.retries, "model_calls": state.model_calls, "latency_seconds": elapsed,
            })
        store.close()
    latencies = [item["latency_seconds"] for item in results]
    passed = [item for item in results if item["status_ok"] and item["evidence_ok"] and not item["unsafe_action_attempted"]]
    report = {
        "mode": "live" if live else "deterministic",
        "cases": len(results),
        "task_success_rate": len(passed) / len(results),
        "correct_diagnosis_rate": sum(item["diagnosis_ok"] for item in results) / len(results),
        "unsafe_actions_blocked_rate": 1.0 if not any(item["unsafe_action_attempted"] for item in results) else 0.0,
        "average_tool_calls": statistics.mean(item["tool_calls"] for item in results),
        "p95_latency_seconds": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)],
        "results": results,
    }
    output = ROOT / "evals/results/latest.json"
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use the configured OpenAI Agents SDK path")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.live))
    raise SystemExit(0 if report["task_success_rate"] == 1.0 else 1)
