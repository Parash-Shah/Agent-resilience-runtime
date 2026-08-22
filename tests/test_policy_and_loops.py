from agent_resilience.loop_detector import LoopDetector
from agent_resilience.models import RiskLevel, ToolName
from agent_resilience.policy import PermissionPolicy
from agent_resilience.decision import DecisionEngine
from agent_resilience.models import AgentDecision, WorkflowState, WorkflowStatus
from agent_resilience.runtime import WorkflowRuntime
from agent_resilience.store import SQLiteStore
from agent_resilience.tools import ToolGateway


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


async def test_blocked_tool_never_reaches_backend(test_settings):
    class BlockedDecision(DecisionEngine):
        async def decide(self, state):
            return AgentDecision(
                action="use_tool",
                tool=ToolName.DELETE_DATABASE,
                arguments={"service": "checkout-service", "environment": "production"},
                rationale="malicious or mistaken destructive request",
            )

    class RecordingBackend:
        calls = 0

        def execute(self, state, tool, arguments, attempt):
            self.calls += 1
            return {"unexpected": True}

    store = SQLiteStore(test_settings.database_path)
    backend = RecordingBackend()
    runtime = WorkflowRuntime(store, BlockedDecision(), ToolGateway(store, backend), LoopDetector())
    state = WorkflowState(task_id="blocked-action", goal="Attempt an operation outside agent authority")
    store.create_workflow(state, 3)
    result = await runtime.process(state.task_id)
    assert result.status == WorkflowStatus.FAILED
    assert backend.calls == 0
    assert "prohibited" in result.last_error.lower()
