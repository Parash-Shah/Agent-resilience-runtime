from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from .errors import PermanentWorkflowError, RetryableWorkflowError
from .models import ToolName, WorkflowState
from .policy import PermissionPolicy, PolicyDecision
from .contracts import RuntimeStore


class ServiceArguments(BaseModel):
    service: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    environment: str = Field(default="production", pattern=r"^(production|staging|test)$")


class DependencyArguments(ServiceArguments):
    dependency: str | None = Field(default=None, max_length=100)


ARGUMENT_MODELS: dict[ToolName, type[BaseModel]] = {
    ToolName.READ_ALERT: ServiceArguments,
    ToolName.INSPECT_METRICS: ServiceArguments,
    ToolName.QUERY_LOGS: ServiceArguments,
    ToolName.DEPENDENCY_HEALTH: DependencyArguments,
    ToolName.RESTART_SERVICE: ServiceArguments,
    ToolName.DELETE_DATABASE: ServiceArguments,
    ToolName.VERIFY_RECOVERY: ServiceArguments,
}


class ToolBackend(Protocol):
    def execute(self, state: WorkflowState, tool: ToolName, arguments: dict[str, Any], attempt: int) -> dict[str, Any]: ...


class ScenarioBackend:
    """Deterministic adapter used locally; real adapters implement the same execute contract."""

    def __init__(self, scenarios_path: str | Path = "fixtures/scenarios.json"):
        self.scenarios = json.loads(Path(scenarios_path).read_text(encoding="utf-8"))

    def execute(self, state: WorkflowState, tool: ToolName, arguments: dict[str, Any], attempt: int) -> dict[str, Any]:
        scenario = self.scenarios.get(state.scenario_id)
        if not scenario:
            raise PermanentWorkflowError(f"unknown scenario: {state.scenario_id}")
        transient_count = int(scenario.get("transient_failures", {}).get(tool.value, 0))
        if 0 < attempt <= transient_count:
            raise RetryableWorkflowError(f"{tool.value} downstream timed out")
        if tool == ToolName.READ_ALERT:
            return scenario["alert"]
        if tool == ToolName.INSPECT_METRICS:
            return scenario["metrics"]
        if tool == ToolName.QUERY_LOGS:
            return scenario["logs"]
        if tool == ToolName.DEPENDENCY_HEALTH:
            return scenario["dependencies"]
        if tool == ToolName.RESTART_SERVICE:
            return {"service": arguments["service"], "restarted": True, "environment": arguments["environment"]}
        if tool == ToolName.VERIFY_RECOVERY:
            restarted = "restart_service" in state.completed_steps
            return scenario["after_restart"] if restarted else scenario["alert"]
        if tool == ToolName.DELETE_DATABASE:
            raise PermanentWorkflowError("blocked tool reached backend")
        raise PermanentWorkflowError(f"unsupported tool: {tool}")


class ToolGateway:
    def __init__(self, store: RuntimeStore, backend: ToolBackend | None = None, policy: PermissionPolicy | None = None):
        self.store = store
        self.backend = backend or ScenarioBackend()
        self.policy = policy or PermissionPolicy()

    def validate_and_decide(self, tool: ToolName, arguments: dict[str, Any]) -> tuple[dict[str, Any], PolicyDecision]:
        try:
            validated = ARGUMENT_MODELS[tool].model_validate(arguments).model_dump()
        except (KeyError, ValidationError) as error:
            raise PermanentWorkflowError(f"invalid arguments for {tool.value}: {error}") from error
        decision = self.policy.evaluate(tool, validated)
        return validated, decision

    def action_id(self, state: WorkflowState, tool: ToolName, arguments: dict[str, Any]) -> str:
        digest = self.store.arguments_hash(arguments)[:16]
        return f"{state.task_id}:{tool.value}:{digest}"

    def execute(self, state: WorkflowState, tool: ToolName, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validated, decision = self.validate_and_decide(tool, arguments)
        if not decision.allowed:
            raise PermanentWorkflowError(decision.reason)
        key = self.action_id(state, tool, validated)
        cached = self.store.get_tool_result(key, validated)
        if cached is not None:
            return cached, True
        attempt = sum(1 for name in state.tool_history if name == tool.value)
        result = self.backend.execute(state, tool, validated, attempt)
        if not isinstance(result, dict) or not result:
            raise RetryableWorkflowError(f"{tool.value} returned invalid structured output")
        self.store.save_tool_result(key, state.task_id, tool.value, validated, result)
        return result, False
