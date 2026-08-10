"""Provider interface and shared failures."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from guarded_agent.domain import ToolAction

type ContextMessage = dict[str, JsonValue]


class LLMProvider(Protocol):
    """Produces exactly one validated action for a supplied conversation."""

    def next_action(self, messages: list[ContextMessage]) -> ToolAction:
        """Return the next action without executing tools or owning a control loop."""


class ProviderResponseError(RuntimeError):
    """Raised when a provider cannot supply one valid action."""
