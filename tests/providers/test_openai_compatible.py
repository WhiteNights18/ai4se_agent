import json

import httpx
import pytest

from guarded_agent.domain import ToolName
from guarded_agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderResponseError,
)


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
