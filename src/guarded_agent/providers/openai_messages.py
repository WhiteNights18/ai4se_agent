"""Translate internal guarded-agent context into OpenAI chat-completions messages."""

from __future__ import annotations

import json

from pydantic import JsonValue

from guarded_agent.providers.base import ContextMessage

_VALID_ROLES = frozenset({"system", "user", "assistant"})

_ACTION_CONTRACT = """Return exactly one strict guarded-agent action as JSON.

Respond with one action object: a "tool" name and an "arguments" object. The available tools
and their arguments are:

{"tool": "list_directory", "arguments": {"path": "<relative posix path>"}}
{"tool": "read_file", "arguments": {"path": "<relative posix path>"}}
{"tool": "search_text", "arguments": {"path": "<relative posix path>", "query": "<text>", "max_results": <1-1000>}}
{"tool": "write_file", "arguments": {"path": "<relative posix path>", "content": "<file content>"}}
{"tool": "delete_file", "arguments": {"path": "<relative posix path>"}}
{"tool": "move_file", "arguments": {"source": "<relative posix path>", "destination": "<relative posix path>"}}
{"tool": "git_status", "arguments": {}}
{"tool": "git_diff", "arguments": {}}
{"tool": "run_command", "arguments": {"argv": ["<command>", "<arg>", ...]}}
{"tool": "run_validator", "arguments": {"argv": ["<command>", "<arg>", ...]}}
{"tool": "save_memory", "arguments": {"category": "<text>", "content": "<text>"}}
{"tool": "retrieve_memory", "arguments": {"query": "<text>", "limit": <1-100>}}
{"tool": "complete", "arguments": {"summary": "<text>"}}
{"tool": "cannot_continue", "arguments": {"reason": "<text>"}}

Path rules: every "path" is a relative POSIX path to an entry strictly inside the workspace. It must not be absolute, must not contain "." or ".." segments, and must not contain backslashes. The workspace root itself cannot be listed or read; to discover what changed use git_status and git_diff, then read or write known files by name (for example "app.py").

Loop semantics: after any write_file, delete_file, move_file, or run_command action, the harness automatically runs the workspace acceptance tests and returns their result as your feedback. When the acceptance tests pass and the goal is met, finish by calling complete with a short summary; do not keep acting after the goal is achieved.

Tool usage: read files with read_file and a relative path; never use run_command to read files, because arbitrary commands require approval and stall the task. Use run_command only for build or utility commands the goal requires.

Emit only the JSON object as the content of your response. No prose and no markdown fences."""


def translate_messages(messages: list[ContextMessage]) -> list[ContextMessage]:
    """Convert internal guarded-agent context into standard OpenAI chat messages.

    Messages that are already valid OpenAI chat messages pass through unchanged. The
    internal user-goal, memory, and tool-turn protocol is expanded into system, user, and
    assistant messages that OpenAI-compatible endpoints accept.
    """
    if all(_is_standard(message) for message in messages):
        return list(messages)
    translated: list[ContextMessage] = []
    system_parts: list[str] = [_ACTION_CONTRACT]
    for message in messages:
        role = message.get("role")
        if role == "system":
            _append_system(message, system_parts)
        elif role == "memory":
            _append_memory(message, system_parts)
        elif role == "user":
            _append_user(message, translated)
        elif role == "tool":
            translated.extend(_serialize_turn(message))
        elif isinstance(role, str) and role in _VALID_ROLES:
            content = message.get("content")
            if isinstance(content, str) and content:
                translated.append({"role": role, "content": content})
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *translated]


def _is_standard(message: ContextMessage) -> bool:
    role = message.get("role")
    if not isinstance(role, str) or role not in _VALID_ROLES:
        return False
    content = message.get("content")
    return isinstance(content, str) and bool(content)


def _append_system(message: ContextMessage, system_parts: list[str]) -> None:
    content = message.get("content")
    if isinstance(content, str) and content:
        system_parts.append(content)


def _append_memory(message: ContextMessage, system_parts: list[str]) -> None:
    entries = message.get("entries")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        category = entry.get("category")
        content = entry.get("content")
        if isinstance(category, str) and isinstance(content, str) and (category or content):
            system_parts.append(f"- [{category}] {content}")


def _append_user(message: ContextMessage, translated: list[ContextMessage]) -> None:
    goal = message.get("goal")
    if isinstance(goal, str) and goal:
        translated.append({"role": "user", "content": goal})
        return
    content = message.get("content")
    if isinstance(content, str) and content:
        translated.append({"role": "user", "content": content})


def _serialize_turn(message: ContextMessage) -> list[ContextMessage]:
    turn = message.get("turn")
    label = f"Turn {turn}" if isinstance(turn, int) else "Turn"
    action = message.get("action")
    feedback = message.get("feedback")
    return [
        {
            "role": "assistant",
            "content": f"{label} action:\n{_json_text(action)}",
        },
        {
            "role": "user",
            "content": f"{label} feedback:\n{_json_text(feedback)}",
        },
    ]


def _json_text(value: JsonValue) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
