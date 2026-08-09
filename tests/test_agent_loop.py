from __future__ import annotations

import sys
from pathlib import Path

from guarded_agent.agent import AgentLoop
from guarded_agent.context import ContextBuilder
from guarded_agent.domain import Feedback, FeedbackKind, Settings, TaskStatus
from guarded_agent.feedback import FeedbackEngine
from guarded_agent.governance import GovernanceEngine
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.redaction import Redactor
from guarded_agent.storage import Database
from guarded_agent.subprocesses import CommandRunner
from guarded_agent.tools import ToolRegistry


def test_failed_validation_changes_next_mock_action(tmp_path: Path) -> None:
    """Catch a loop that omits validation feedback from the next provider context."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    acceptance = [
        [
            sys.executable,
            "-c",
            "from pathlib import Path; raise SystemExit(not Path('fixed.txt').exists())",
        ]
    ]
    settings = Settings(validation_commands=acceptance)
    database = Database.open(tmp_path / "agent.sqlite3")
    registered = database.tasks.create_workspace(str(workspace.resolve()), "workspace")
    task = database.tasks.create_task(
        task_id="task-1",
        workspace_id=registered.id,
        goal="create the marker",
        acceptance_commands=acceptance,
        limits=settings.model_dump(mode="json"),
    )
    provider = ScriptedMockProvider(
        default={"tool": "write_file", "arguments": {"path": "wrong.txt", "content": "no"}},
        on_feedback={
            "TEST_FAILURE": {
                "tool": "write_file",
                "arguments": {"path": "fixed.txt", "content": "yes"},
            },
            "PASS": {"tool": "complete", "arguments": {"summary": "marker created"}},
        },
    )
    runner = CommandRunner(redactor=Redactor([]))
    loop = AgentLoop(
        database=database,
        task_id=task.id,
        workspace=workspace,
        settings=settings,
        provider=provider,
        tools=ToolRegistry(workspace, settings, Redactor([]), runner=runner),
        feedback=FeedbackEngine(runner, configured_commands=acceptance),
        governance=GovernanceEngine(
            workspace=workspace, settings=settings, database=database, task_id=task.id
        ),
    )

    assert loop.run() is TaskStatus.COMPLETED
    assert (workspace / "fixed.txt").read_text() == "yes"
    assert any(
        message.get("feedback_kind") == "TEST_FAILURE"
        for messages in provider.messages
        for message in messages
    )
    database.close()


def test_context_limits_history_to_eight_turns_and_memory_to_ten(tmp_path: Path) -> None:
    """Catch a provider context that grows with the full database history."""
    database = Database.open(tmp_path / "agent.sqlite3")
    workspace = database.tasks.create_workspace(str(tmp_path.resolve()), "workspace")
    task = database.tasks.create_task(
        task_id="task-1",
        workspace_id=workspace.id,
        goal="remember convention",
        acceptance_commands=[],
        limits={},
    )
    feedback = Feedback(kind=FeedbackKind.PASS, message="ok", command_result=None, can_continue=True)
    for turn_no in range(1, 13):
        database.tasks.add_turn(
            task.id,
            turn_no,
            {"tool": "git_status", "arguments": {}},
            feedback.model_dump(mode="json"),
        )
        database.memory.add(workspace.id, "convention", f"remember convention {turn_no}", "user", "confirmed")

    messages = ContextBuilder(database.memory).build(task, database.tasks.list_turns(task.id))

    history = [message for message in messages if message.get("role") == "tool"]
    memory = next(message for message in messages if message.get("role") == "memory")
    assert [message["turn"] for message in history] == list(range(5, 13))
    assert len(memory["entries"]) == 10
    database.close()


def test_malformed_provider_response_becomes_feedback_not_a_tool_call(tmp_path: Path) -> None:
    """Catch a loop trusting a provider response without re-parsing the strict action schema."""

    class MalformedProvider:
        def next_action(self, messages: list[object]) -> object:
            return {"tool": "write_file", "arguments": {"path": "x", "content": "ok", "extra": 1}}

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database.open(tmp_path / "agent.sqlite3")
    registered = database.tasks.create_workspace(str(workspace.resolve()), "workspace")
    task = database.tasks.create_task(
        task_id="task-1", workspace_id=registered.id, goal="do work", acceptance_commands=[], limits={}
    )
    settings = Settings()
    runner = CommandRunner(redactor=Redactor([]))
    loop = AgentLoop(
        database=database,
        task_id=task.id,
        workspace=workspace,
        settings=settings,
        provider=MalformedProvider(),  # type: ignore[arg-type]
        tools=ToolRegistry(workspace, settings, Redactor([]), runner=runner),
        feedback=FeedbackEngine(runner, configured_commands=[]),
        governance=GovernanceEngine(
            workspace=workspace, settings=settings, database=database, task_id=task.id
        ),
    )

    assert loop.step() is TaskStatus.RUNNING
    assert not (workspace / "x").exists()
    assert database.tasks.list_turns(task.id)[0].feedback_json["kind"] == "INVALID_ACTION"
    database.close()
