"""Deterministic, offline demonstrations of the core safety mechanisms."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from guarded_agent.domain import TaskStatus
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.service import ApplicationService
from guarded_agent.storage import Database


def run_demo() -> tuple[str, ...]:
    """Run the three fixed scenarios in an isolated temporary workspace."""
    with tempfile.TemporaryDirectory(prefix="guarded-agent-demo-") as temporary_directory:
        root = Path(temporary_directory)
        _dangerous_action_is_blocked(root / "danger")
        _feedback_correction_passes(root / "feedback")
        _approval_tampering_is_blocked(root / "approval")
    return (
        "dangerous action blocked",
        "feedback correction passed",
        "approval tampering blocked",
    )


def _database(root: Path) -> Database:
    root.mkdir()
    return Database.open(root / "agent.sqlite3")


def _dangerous_action_is_blocked(workspace: Path) -> None:
    database = _database(workspace)
    try:
        secret = workspace / ".env"
        secret.write_text("DEMO_SECRET=not-used", encoding="utf-8")
        service = ApplicationService(database)
        task = service.create(
            workspace,
            "delete a protected file",
            [],
            ScriptedMockProvider({"tool": "delete_file", "arguments": {"path": ".env"}}),
        )
        assert service.run(task.id) is TaskStatus.FAILED
        assert secret.exists()
    finally:
        database.close()


def _feedback_correction_passes(workspace: Path) -> None:
    database = _database(workspace)
    try:
        validator = workspace / "validate-marker"
        validator.write_text("#!/bin/sh\ntest -f fixed.txt\n", encoding="utf-8")
        validator.chmod(0o700)
        acceptance = [[str(validator)]]
        (workspace / "guarded-agent.toml").write_text(
            "[validation]\ncommands = " + json.dumps(acceptance) + "\n",
            encoding="utf-8",
        )
        service = ApplicationService(database)
        task = service.create(
            workspace,
            "create the marker",
            acceptance,
            ScriptedMockProvider(
                default={"tool": "write_file", "arguments": {"path": "wrong.txt", "content": "no"}},
                on_feedback={
                    "TEST_FAILURE": {
                        "tool": "write_file",
                        "arguments": {"path": "fixed.txt", "content": "yes"},
                    },
                    "PASS": {"tool": "complete", "arguments": {"summary": "marker created"}},
                },
            ),
        )
        assert service.run(task.id) is TaskStatus.COMPLETED
        assert (workspace / "fixed.txt").is_file()
    finally:
        database.close()


def _approval_tampering_is_blocked(workspace: Path) -> None:
    database = _database(workspace)
    try:
        first = workspace / "one.txt"
        second = workspace / "two.txt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        service = ApplicationService(database)
        task = service.create(
            workspace,
            "delete one file",
            [],
            ScriptedMockProvider({"tool": "delete_file", "arguments": {"path": "one.txt"}}),
        )
        assert service.run(task.id) is TaskStatus.WAITING_APPROVAL
        approval_id = service.pending_approval_id(task.id)
        database.connection.execute(
            "UPDATE pending_actions SET action_json = ? WHERE approval_id = ?",
            ('{"arguments":{"path":"two.txt"},"tool":"delete_file"}', approval_id),
        )
        database.approvals.approve(approval_id, datetime.now(UTC))
        assert service.resume(task.id, approval_id) is TaskStatus.RUNNING
        assert first.exists() and second.exists()
        assert any(event.event_type == "approval_mismatch" for event in database.audit.list_for_task(task.id))
    finally:
        database.close()
