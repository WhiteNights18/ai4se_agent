"""Bounded provider context assembled from persisted task history and memory."""

from __future__ import annotations

from pydantic import JsonValue

from guarded_agent.memory import MemoryStore
from guarded_agent.providers.base import ContextMessage
from guarded_agent.storage import AgentTurn, ConversationMessage, Task


class ContextBuilder:
    """Build the deliberately small, serializable context accepted by providers."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    def build(
        self,
        task: Task,
        turns: list[AgentTurn],
        conversation: list[ConversationMessage] | None = None,
    ) -> list[ContextMessage]:
        memories = self._memory.search(task.workspace_id, task.goal, limit=10)
        messages: list[ContextMessage] = [
            {
                "role": "system",
                "content": "Return exactly one strict guarded-agent action.",
            },
            {"role": "user", "goal": task.goal},
        ]
        if memories:
            messages.append(
                {
                    "role": "memory",
                    "entries": [
                        {"category": memory.category, "content": memory.content}
                        for memory in memories[:10]
                    ],
                }
            )
        for message in (conversation or [])[-12:]:
            messages.append(
                {
                    "role": "user" if message.role == "user" else "assistant",
                    "content": message.content,
                }
            )
        for turn in turns[-8:]:
            messages.append(_turn_message(turn))
        return messages


def _turn_message(turn: AgentTurn) -> ContextMessage:
    message: dict[str, JsonValue] = {
        "role": "tool",
        "turn": turn.turn_no,
        "action": turn.action_json,
        "feedback": turn.feedback_json,
    }
    kind = turn.feedback_json.get("kind")
    if isinstance(kind, str):
        message["feedback_kind"] = kind
    return message
