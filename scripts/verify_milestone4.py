from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_resilience.config import Settings


EXPECTED_STEPS = [
    "read_alert",
    "inspect_metrics",
    "query_logs",
    "dependency_health",
    "restart_service",
    "verify_recovery",
]


def request_json(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict | list:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1_000]
        raise RuntimeError(f"{method} {url} failed with HTTP {error.code}: {detail}") from error


def wait_for_state(
    incident_url: str,
    token: str,
    predicate: Callable[[dict], bool],
    timeout: float,
    description: str,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = request_json(incident_url, token)
        if predicate(last):
            return last
        if last.get("status") in {"FAILED", "DEAD_LETTERED", "LOOP_STOPPED", "HUMAN_REJECTED"}:
            raise RuntimeError(f"workflow became {last['status']} while waiting for {description}: {last.get('last_error')}")
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {description}; last state={last.get('status')} {last.get('completed_steps')}")


def task_id(task_arn: str) -> str:
    return task_arn.rsplit("/", 1)[-1]


def verify(args: argparse.Namespace) -> dict:
    config = Settings()
    token = os.getenv("ADMIN_API_TOKEN") or config.admin_api_token
    if not token:
        raise RuntimeError("ADMIN_API_TOKEN must be available in the environment or ignored .env.local")

    session = boto3.session.Session(region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    ecs = session.client("ecs")
    api = args.api_url.rstrip("/")
    created = request_json(
        f"{api}/v1/incidents",
        token,
        "POST",
        {"goal": args.goal, "scenario_id": "checkout-pool-exhaustion"},
    )
    incident_id = created["task_id"]
    incident_url = f"{api}/v1/incidents/{incident_id}"

    paused = wait_for_state(
        incident_url,
        token,
        lambda state: len(state.get("completed_steps", [])) == 3 and state.get("current_step") is not None,
        args.timeout,
        "the deterministic step-four chaos window",
    )
    checkpoint_steps = paused["completed_steps"]
    resumed_step = paused["current_step"]
    running = ecs.list_tasks(cluster=args.cluster, serviceName=args.worker_service, desiredStatus="RUNNING")["taskArns"]
    if len(running) != 1:
        raise RuntimeError(f"recovery proof requires exactly one running worker before termination; found {len(running)}")
    stopped_task = running[0]
    ecs.stop_task(cluster=args.cluster, task=stopped_task, reason=f"AgentResilience recovery proof {incident_id}")

    resumed = wait_for_state(
        incident_url,
        token,
        lambda state: state.get("completed_steps", [])[:4] == checkpoint_steps + [resumed_step],
        args.timeout,
        "replacement worker to resume from step four",
    )
    replacement_deadline = time.monotonic() + args.timeout
    replacement_task = None
    while time.monotonic() < replacement_deadline:
        candidates = ecs.list_tasks(
            cluster=args.cluster,
            serviceName=args.worker_service,
            desiredStatus="RUNNING",
        )["taskArns"]
        replacement_task = next((item for item in candidates if item != stopped_task), None)
        if replacement_task:
            break
        time.sleep(1)
    if replacement_task is None:
        raise TimeoutError("ECS did not start a replacement worker task")

    waiting = wait_for_state(
        incident_url,
        token,
        lambda state: state.get("status") == "WAITING_FOR_APPROVAL",
        args.timeout,
        "production restart approval",
    )
    request_json(
        f"{incident_url}/approve",
        token,
        "POST",
        {"actor": args.actor, "reason": "Milestone 4 evidence-backed controlled recovery demonstration"},
    )
    completed = wait_for_state(
        incident_url,
        token,
        lambda state: state.get("status") == "COMPLETED",
        args.timeout,
        "approved restart and recovery verification",
    )
    events = request_json(f"{incident_url}/events", token)
    completed_tools = [event["payload"].get("tool") for event in events if event["event_type"] == "TOOL_COMPLETED"]
    if len(completed["completed_steps"]) != len(EXPECTED_STEPS) or set(completed["completed_steps"]) != set(EXPECTED_STEPS):
        raise RuntimeError(f"unexpected completed step sequence: {completed['completed_steps']}")
    if completed_tools.count("restart_service") != 1:
        raise RuntimeError("idempotency proof failed: restart_service did not complete exactly once")
    if not any(event["event_type"] == "APPROVAL_GRANTED" for event in events):
        raise RuntimeError("approval audit event is missing")
    if resumed["completed_steps"][:4] != checkpoint_steps + [resumed_step] or paused["tool_calls"] != 3:
        raise RuntimeError("workflow did not resume from the last known good checkpoint")

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "PASS",
        "aws_identity_verified": bool(identity.get("Account")),
        "region": args.region,
        "incident_id": incident_id,
        "stopped_worker_task": task_id(stopped_task),
        "replacement_worker_task": task_id(replacement_task),
        "checkpoint_before_kill": checkpoint_steps,
        "resumed_step": resumed_step,
        "final_status": completed["status"],
        "completed_steps": completed["completed_steps"],
        "restart_side_effect_count": completed_tools.count("restart_service"),
        "approval_audited": True,
        "model_calls": completed["model_calls"],
        "tool_calls": completed["tool_calls"],
        "retries": completed["retries"],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deployed Milestone 4 worker-kill recovery proof.")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--worker-service", required=True)
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--actor", default="milestone4-verifier")
    parser.add_argument(
        "--goal",
        default="Investigate why checkout-service has elevated production errors and safely restore service.",
    )
    parser.add_argument("--output", default="evidence/milestone4/latest.json")
    args = parser.parse_args()
    report = verify(args)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
