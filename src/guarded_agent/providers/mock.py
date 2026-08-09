"""A deterministic provider for tests and local scripted flows."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from guarded_agent.domain import ToolAction, parse_tool_action

from .base import ContextMessage, ProviderResponseError

type ActionInput = ToolAction | dict[str, JsonValue]


class ScriptedMockProvider:
    """Return a default action or branch once on the latest feedback message."""

    def __init__(
        self,
        default: ActionInput | None = None,
        *,
        on_feedback: Mapping[str, ActionInput] | None = None,
    ) -> None:
        self._default = _parse_action(default) if default is not None else None
        self._on_feedback = {
            kind: _parse_action(action) for kind, action in (on_feedback or {}).items()
        }
        self.messages: list[list[ContextMessage]] = []

    def next_action(self, messages: list[ContextMessage]) -> ToolAction:
        """Record messages and choose a preconfigured action; never execute it."""
        self.messages.append(messages)
        feedback_kind = _latest_feedback_kind(messages)
        if feedback_kind is not None and feedback_kind in self._on_feedback:
            return self._on_feedback[feedback_kind]
        if self._default is not None:
            return self._default
        raise ProviderResponseError("no scripted action configured")


def _parse_action(value: ActionInput) -> ToolAction:
    if isinstance(value, dict):
        return parse_tool_action(value)
    return value


def _latest_feedback_kind(messages: list[ContextMessage]) -> str | None:
    for message in reversed(messages):
        feedback_kind = message.get("feedback_kind")
        if isinstance(feedback_kind, str):
            return feedback_kind
    return None
