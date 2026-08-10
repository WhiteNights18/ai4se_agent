from guarded_agent.domain import ToolName
from guarded_agent.providers.base import ContextMessage
from guarded_agent.providers.mock import ScriptedMockProvider


def test_mock_provider_records_messages_and_branches_on_feedback() -> None:
    provider = ScriptedMockProvider(
        default={"tool": "complete", "arguments": {"summary": "done"}},
        on_feedback={
            "TEST_FAILURE": {
                "tool": "write_file",
                "arguments": {"path": "fixed.py", "content": "ok"},
            }
        },
    )
    messages: list[ContextMessage] = [{"role": "tool", "feedback_kind": "TEST_FAILURE"}]

    action = provider.next_action(messages)

    assert action.tool is ToolName.WRITE_FILE
    assert action.arguments.path == "fixed.py"
    assert provider.messages == [messages]
