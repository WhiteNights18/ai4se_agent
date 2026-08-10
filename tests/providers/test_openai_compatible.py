import json

import httpx
import pytest

from guarded_agent.domain import ToolName
from guarded_agent.providers.base import ContextMessage
from guarded_agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderResponseError,
)
from guarded_agent.providers.openai_messages import translate_messages


def _provider(
    handler: httpx.MockTransport, *, max_retries: int = 0
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        endpoint="https://provider.invalid/v1",
        api_key="test-key",
        model="test-model",
        max_retries=max_retries,
        transport=handler,
    )


def test_http_provider_sends_messages_and_parses_strict_action() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "tool": "write_file",
                                    "arguments": {"path": "fixed.py", "content": "ok"},
                                }
                            )
                        }
                    }
                ]
            },
        )

    action = _provider(httpx.MockTransport(handler)).next_action(
        [{"role": "user", "content": "fix it"}]
    )

    assert action.tool is ToolName.WRITE_FILE
    assert action.arguments.path == "fixed.py"
    assert len(seen_requests) == 1
    assert seen_requests[0].url == "https://provider.invalid/v1/chat/completions"
    assert seen_requests[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(seen_requests[0].content) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "fix it"}],
    }


def test_http_provider_rejects_non_action_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    with pytest.raises(ProviderResponseError):
        _provider(httpx.MockTransport(handler)).next_action([])


def test_http_provider_uses_one_request_for_http_failure() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500, json={})

    with pytest.raises(ProviderResponseError):
        _provider(httpx.MockTransport(handler)).next_action([])

    assert requests == 1


def _internal_messages() -> list[ContextMessage]:
    return [
        {"role": "system", "content": "Return exactly one strict guarded-agent action."},
        {"role": "user", "goal": "fix the bug"},
        {
            "role": "memory",
            "entries": [{"category": "convention", "content": "use tabs"}],
        },
        {
            "role": "tool",
            "turn": 1,
            "action": {
                "tool": "write_file",
                "arguments": {"path": "a.py", "content": "x"},
            },
            "feedback": {
                "kind": "PASS",
                "message": "ok",
                "command_result": None,
                "can_continue": True,
            },
            "feedback_kind": "PASS",
        },
        {
            "role": "tool",
            "turn": 2,
            "action": {"tool": "git_status", "arguments": {}},
            "feedback": {
                "kind": "TEST_FAILURE",
                "message": "tests fail",
                "command_result": None,
                "can_continue": True,
            },
            "feedback_kind": "TEST_FAILURE",
        },
    ]


def test_provider_translates_internal_messages_to_openai_schema() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"tool":"complete","arguments":{"summary":"done"}}'
                        }
                    }
                ]
            },
        )

    action = _provider(httpx.MockTransport(handler)).next_action(_internal_messages())

    assert action.tool is ToolName.COMPLETE
    body = json.loads(seen_requests[0].content)
    messages = body["messages"]
    assert messages
    for message in messages:
        assert message["role"] in {"system", "user", "assistant"}
        content = message["content"]
        assert isinstance(content, str) and content
    contents = [str(message["content"]) for message in messages]
    assert any("fix the bug" in content for content in contents)
    assert any("convention" in content for content in contents)
    assert any('"write_file"' in content for content in contents)
    assert any('"git_status"' in content for content in contents)
    assert any('"TEST_FAILURE"' in content for content in contents)


def test_translate_messages_expands_internal_protocol_in_order() -> None:
    translated = translate_messages(
        [
            {"role": "user", "goal": "add docs"},
            {
                "role": "memory",
                "entries": [{"category": "style", "content": "prefer lowercase"}],
            },
            {
                "role": "tool",
                "turn": 7,
                "action": {"tool": "git_status", "arguments": {}},
                "feedback": {
                    "kind": "PASS",
                    "message": "ok",
                    "command_result": None,
                    "can_continue": True,
                },
                "feedback_kind": "PASS",
            },
        ]
    )

    assert [message["role"] for message in translated] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    system_content = str(translated[0]["content"])
    assert system_content.startswith("Return exactly one strict")
    assert "prefer lowercase" in system_content
    assert translated[1] == {"role": "user", "content": "add docs"}
    assert '"git_status"' in str(translated[2]["content"])
    assert '"PASS"' in str(translated[3]["content"])


def test_http_provider_parses_markdown_fenced_action_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"tool":"write_file","arguments":{"path":"a.py","content":"ok"}}\n```'
                        }
                    }
                ]
            },
        )

    action = _provider(httpx.MockTransport(handler)).next_action([])

    assert action.tool is ToolName.WRITE_FILE
    assert action.arguments.path == "a.py"


def test_http_provider_parses_action_json_wrapped_in_prose() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": 'Done. Here is my action: {"tool":"complete","arguments":{"summary":"fixed"}}. That is all.'
                        }
                    }
                ]
            },
        )

    action = _provider(httpx.MockTransport(handler)).next_action([])

    assert action.tool is ToolName.COMPLETE
    assert action.arguments.summary == "fixed"


def test_http_provider_retries_a_transient_request_before_returning_an_action() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.ReadTimeout("slow provider", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"tool":"complete","arguments":{}}'}}
                ]
            },
        )

    action = _provider(httpx.MockTransport(handler), max_retries=1).next_action([])

    assert action.tool is ToolName.COMPLETE
    assert requests == 2
