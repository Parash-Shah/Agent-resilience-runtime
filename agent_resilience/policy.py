from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import RiskLevel, ToolName


@dataclass(frozen=True)
class PolicyDecision:
    risk: RiskLevel
    allowed: bool
    approval_required: bool
    reason: str


class PermissionPolicy:
    """Fail-closed policy boundary independent from model instructions."""

    def evaluate(self, tool: ToolName, arguments: dict[str, Any]) -> PolicyDecision:
        if tool == ToolName.DELETE_DATABASE:
            return PolicyDecision(RiskLevel.BLOCKED, False, False, "database deletion is prohibited")
        if tool == ToolName.RESTART_SERVICE:
            environment = str(arguments.get("environment", "production")).lower()
            if environment == "production":
                return PolicyDecision(RiskLevel.HIGH, True, True, "production restart requires human approval")
            return PolicyDecision(RiskLevel.MEDIUM, True, False, "non-production restart is permitted")
        return PolicyDecision(RiskLevel.LOW, True, False, "read-only or verification action")
