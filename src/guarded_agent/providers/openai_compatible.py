"""One-shot OpenAI-compatible chat-completions provider."""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from guarded_agent.domain import ToolAction, parse_tool_action

from .base import ContextMessage, ProviderResponseError
from .openai_messages import translate_messages

__all__ = ["OpenAICompatibleProvider", "ProviderResponseError"]

_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_DEFAULT_READ_TIMEOUT_SECONDS = 30.0
_MAX_RETRIES = 3


class OpenAICompatibleProvider:
    """Call one chat-completions endpoint and return one strictly parsed action."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        connect_timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = _DEFAULT_READ_TIMEOUT_SECONDS,
        max_retries: int = 0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not endpoint or not api_key or not model:
            raise ValueError("endpoint, api_key, and model must be non-empty")
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if not 0 <= max_retries <= _MAX_RETRIES:
            raise ValueError(f"max_retries must be between 0 and {_MAX_RETRIES}")
        self._url = f"{endpoint.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=connect_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._max_retries = max_retries
        self._transport = transport

    def next_action(self, messages: list[ContextMessage]) -> ToolAction:
        """Request and validate a single action, with bounded transient retries only."""
        for attempt in range(self._max_retries + 1):
            try:
                response = self._post(messages)
            except httpx.RequestError as error:
                if attempt < self._max_retries:
                    continue
                raise ProviderResponseError("provider request failed") from error
            if response.status_code >= 500 and attempt < self._max_retries:
                continue
            if response.is_error:
                raise ProviderResponseError(f"provider returned HTTP {response.status_code}")
            return _parse_response(response)
        raise AssertionError("provider retry loop ended unexpectedly")

    def _post(self, messages: list[ContextMessage]) -> httpx.Response:
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            return client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "messages": translate_messages(messages)},
            )


def _parse_response(response: httpx.Response) -> ToolAction:
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content must be a JSON string")
        action = _extract_json(content)
        return parse_tool_action(action)
    except (IndexError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise ProviderResponseError("provider response did not contain a valid tool action") from error


def _extract_json(content: str) -> object:
    """Parse the first complete JSON object in model content, ignoring prose."""
    start = content.find("{")
    if start == -1:
        raise ValueError("content contains no JSON object")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start : index + 1])
    raise ValueError("content contains no complete JSON object")
