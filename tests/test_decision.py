from __future__ import annotations

import pytest
from agents import AgentOutputSchema
from agents.exceptions import UserError
from pydantic import ValidationError

from agent_resilience.decision import OpenAIDecisionEngine
from agent_resilience.errors import PermanentWorkflowError
from agent_resilience.models import AgentDecision, ToolName


def test_agent_decision_is_a_valid_strict_agents_sdk_schema():
    schema = AgentOutputSchema(AgentDecision).json_schema()

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["AgentToolArguments"]["additionalProperties"] is False


def test_agent_decision_rejects_unbounded_tool_arguments():
    with pytest.raises(ValidationError):
        AgentDecision(
            action="use_tool",
            rationale="attempt an unbounded operation",
            tool=ToolName.READ_ALERT,
            arguments={"service": "checkout-service", "shell_command": "unexpected"},
        )


def test_agents_sdk_configuration_errors_are_not_retried():
    classified = OpenAIDecisionEngine.classify_error(UserError("invalid strict schema"))

    assert isinstance(classified, PermanentWorkflowError)
