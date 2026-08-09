import pytest
from pydantic import ValidationError

from guarded_agent.domain import (
    Action,
    Feedback,
    FeedbackKind,
    GovernanceDecision,
    GovernanceOutcome,
    TaskStatus,
    ToolAction,
    ToolName,
    ToolResult,
    WriteFileArguments,
    parse_tool_action,
)


def test_action_rejects_unknown_fields() -> None:
    """Catch an action envelope that silently accepts attacker-supplied fields."""
    with pytest.raises(ValidationError):
        Action.model_validate(
            {"tool": "read_file", "arguments": {"path": "README.md"}, "surprise": 1}
        )


def test_action_rejects_unknown_tool() -> None:
    """Catch a tool envelope that can introduce unregistered tools."""
    with pytest.raises(ValidationError):
        Action.model_validate({"tool": "network_fetch", "arguments": {}})


def test_action_serializes_the_declared_tool_name() -> None:
    """Catch a model that fails to preserve the string-valued tool contract."""
    action = Action.model_validate({"tool": "read_file", "arguments": {"path": "README.md"}})

    assert action.tool is ToolName.READ_FILE
    assert action.model_dump(mode="json") == {
        "tool": "read_file",
        "arguments": {"path": "README.md"},
    }


def test_public_tool_action_parse_rejects_per_tool_extra_fields() -> None:
    """Catch provider-facing parsing leaving argument validation until execution time."""
    with pytest.raises(ValidationError):
        parse_tool_action(
            {
                "tool": "write_file",
                "arguments": {"path": "x.txt", "content": "x", "surprise": True},
            }
        )


def test_public_tool_action_exposes_typed_arguments() -> None:
    """Catch the public discriminated contract degrading back to an untyped dictionary."""
    parsed: ToolAction = parse_tool_action(
        {"tool": "write_file", "arguments": {"path": "x.txt", "content": "x"}}
    )

    assert parsed.tool is ToolName.WRITE_FILE
    assert isinstance(parsed.arguments, WriteFileArguments)


def test_tool_result_and_feedback_keep_the_full_cross_module_payload() -> None:
    """Catch DTOs that drop execution fields needed by the feedback loop."""
    result = ToolResult(
        tool=ToolName.RUN_VALIDATOR,
        exit_code=1,
        stdout="",
        stderr="failed",
        stdout_truncated=False,
        stderr_truncated=True,
        duration_ms=23,
        changes=["src/example.py"],
    )
    feedback = Feedback(
        kind=FeedbackKind.TEST_FAILURE,
        message="validation failed",
        command_result=result,
        can_continue=True,
    )

    assert feedback.model_dump(mode="json") == {
        "kind": "TEST_FAILURE",
        "message": "validation failed",
        "command_result": {
            "tool": "run_validator",
            "exit_code": 1,
            "stdout": "",
            "stderr": "failed",
            "stdout_truncated": False,
            "stderr_truncated": True,
            "duration_ms": 23,
            "changes": ["src/example.py"],
        },
        "can_continue": True,
    }


def test_governance_decision_and_status_expose_only_declared_values() -> None:
    """Catch governance contracts that accept hidden state or invalid outcomes."""
    decision = GovernanceDecision(
        outcome=GovernanceOutcome.REQUIRE_APPROVAL,
        rule_id="destructive_file_change",
        reason="deleting a file changes project state",
        action_digest="a" * 64,
        approval_id="approval-1",
    )

    assert decision.model_dump(mode="json")["outcome"] == "REQUIRE_APPROVAL"
    assert TaskStatus.WAITING_APPROVAL.value == "WAITING_APPROVAL"
    with pytest.raises(ValidationError):
        GovernanceDecision.model_validate(
            {
                "outcome": "ALLOW",
                "rule_id": "read_only",
                "reason": "safe",
                "action_digest": None,
                "approval_id": None,
                "undocumented": True,
            }
        )
