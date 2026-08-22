from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"
    LOOP_STOPPED = "LOOP_STOPPED"
    HUMAN_REJECTED = "HUMAN_REJECTED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


class ToolName(StrEnum):
    READ_ALERT = "read_alert"
    INSPECT_METRICS = "inspect_metrics"
    QUERY_LOGS = "query_logs"
    DEPENDENCY_HEALTH = "dependency_health"
    RESTART_SERVICE = "restart_service"
    DELETE_DATABASE = "delete_database"
    VERIFY_RECOVERY = "verify_recovery"


class PendingAction(BaseModel):
    action_id: str
    tool: ToolName
    arguments: dict[str, Any]
    risk: RiskLevel
    rationale: str
    created_at: datetime = Field(default_factory=utc_now)


class WorkflowState(BaseModel):
    task_id: str
    goal: str
    scenario_id: str = "checkout-pool-exhaustion"
    status: WorkflowStatus = WorkflowStatus.QUEUED
    completed_steps: list[str] = Field(default_factory=list)
    current_step: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    tool_history: list[str] = Field(default_factory=list)
    progress_history: list[str] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    diagnosis: str | None = None
    remediation: str | None = None
    final_answer: str | None = None
    last_error: str | None = None
    retries: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    version: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def terminal(self) -> bool:
        return self.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.DEAD_LETTERED,
            WorkflowStatus.LOOP_STOPPED,
            WorkflowStatus.HUMAN_REJECTED,
        }


class AgentDecision(BaseModel):
    action: Literal["use_tool", "complete", "fail"]
    rationale: str = Field(min_length=1, max_length=1_000)
    tool: ToolName | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    diagnosis: str | None = None
    remediation: str | None = None
    final_answer: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "AgentDecision":
        if self.action == "use_tool" and self.tool is None:
            raise ValueError("tool is required when action is use_tool")
        if self.action == "complete" and not self.final_answer:
            raise ValueError("final_answer is required when action is complete")
        return self


class CreateIncidentRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=2_000)
    scenario_id: str = Field(default="checkout-pool-exhaustion", pattern=r"^[a-zA-Z0-9_-]+$")


class ApprovalRequest(BaseModel):
    actor: str = Field(default="human", min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1_000)


class EventRecord(BaseModel):
    id: int | str
    task_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class QueueDelivery(BaseModel):
    id: int | str
    task_id: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    receipt_handle: str | None = None
