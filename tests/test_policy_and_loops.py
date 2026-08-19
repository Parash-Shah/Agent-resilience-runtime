from agent_resilience.loop_detector import LoopDetector
from agent_resilience.models import RiskLevel, ToolName
from agent_resilience.policy import PermissionPolicy


def test_policy_enforces_risk_boundaries():
    policy = PermissionPolicy()
    assert policy.evaluate(ToolName.READ_ALERT, {}).risk == RiskLevel.LOW
    production = policy.evaluate(ToolName.RESTART_SERVICE, {"environment": "production"})
    assert production.allowed and production.approval_required and production.risk == RiskLevel.HIGH
    test = policy.evaluate(ToolName.RESTART_SERVICE, {"environment": "test"})
    assert test.allowed and not test.approval_required and test.risk == RiskLevel.MEDIUM
    deletion = policy.evaluate(ToolName.DELETE_DATABASE, {})
    assert not deletion.allowed and deletion.risk == RiskLevel.BLOCKED


def test_loop_detector_finds_tool_patterns_and_no_progress():
    detector = LoopDetector(repeat_threshold=4)
    sequence = ["logs", "metrics"] * 4
    assert detector.reason(sequence, ["a", "b"]) == "repeated tool sequence detected"
    assert detector.reason(["a", "b", "c"], ["same"] * 4) == "agent repeated actions without changing workflow evidence"
    assert detector.reason(["alert", "metrics", "logs"], ["1", "2", "3"]) is None
