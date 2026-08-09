from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from guarded_agent.domain import TaskStatus
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.service import ApplicationService
from guarded_agent.storage import ApprovalStatus, Database


def test_approval_pauses_and_resume_executes_persisted_original_action(tmp_path: Path) -> None:
    """Catch a resume path that requests a fresh action or executes before approval."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "obsolete.txt"
    target.write_text("obsolete")
    database = Database.open(tmp_path / "agent.sqlite3")
    service = ApplicationService(database)
    task = service.create(
        workspace,
        "remove obsolete file",
        [],
        ScriptedMockProvider(
            {"tool": "delete_file", "arguments": {"path": "obsolete.txt"}}
        ),
    )

    assert service.run(task.id) is TaskStatus.WAITING_APPROVAL
    assert target.exists()
    approval_id = service.pending_approval_id(task.id)
    database.approvals.approve(approval_id, datetime.now(UTC))

    assert service.resume(task.id, approval_id) is TaskStatus.RUNNING
    assert not target.exists()
    assert database.approvals.get(approval_id).status is ApprovalStatus.CONSUMED
    database.close()


def test_completion_cannot_bypass_unconfigured_or_failing_acceptance(tmp_path: Path) -> None:
    """Catch a complete action that skips the startup validation-command snapshot."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = [sys.executable, "-c", "raise SystemExit(1)"]
    database = Database.open(tmp_path / "agent.sqlite3")
    service = ApplicationService(database, configured_validation_commands=[command])
    task = service.create(
        workspace,
        "claim completion",
        command and [command],
        ScriptedMockProvider({"tool": "complete", "arguments": {"summary": "done"}}),
    )

    assert service.run(task.id) is not TaskStatus.COMPLETED
    assert database.tasks.get(task.id).status is not TaskStatus.COMPLETED
    database.close()


def test_tampered_persisted_action_cannot_use_an_approved_action(tmp_path: Path) -> None:
    """Catch approval resume that executes stored parameters without a fresh digest check."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "one.txt").write_text("one")
    (workspace / "two.txt").write_text("two")
    database = Database.open(tmp_path / "agent.sqlite3")
    service = ApplicationService(database)
    task = service.create(
        workspace,
        "remove file",
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
    assert (workspace / "one.txt").exists()
    assert (workspace / "two.txt").exists()
    assert any(event.event_type == "approval_mismatch" for event in database.audit.list_for_task(task.id))
    database.close()


def test_create_rejects_acceptance_outside_configured_snapshot(tmp_path: Path) -> None:
    """Catch task creation that lets persisted acceptance argv introduce arbitrary commands."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configured = [sys.executable, "-c", "print('ok')"]
    injected = [sys.executable, "-c", "raise SystemExit(1)"]
    database = Database.open(tmp_path / "agent.sqlite3")
    service = ApplicationService(database, configured_validation_commands=[configured])

    try:
        service.create(
            workspace,
            "do not run injected command",
            [injected],
            ScriptedMockProvider({"tool": "complete", "arguments": {}}),
        )
    except ValueError as error:
        assert "configured" in str(error)
    else:
        raise AssertionError("unconfigured acceptance command was accepted")
    database.close()


def test_cancel_moves_a_created_task_through_running_to_cancelled(tmp_path: Path) -> None:
    """Catch cancellation that bypasses the persisted lifecycle graph."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database.open(tmp_path / "agent.sqlite3")
    service = ApplicationService(database)
    task = service.create(
        workspace,
        "do not start",
        [],
        ScriptedMockProvider({"tool": "complete", "arguments": {}}),
    )

    assert service.cancel(task.id) is TaskStatus.CANCELLED
    assert [event.event_type for event in database.audit.list_for_task(task.id)] == [
        "task_started",
        "task_cancelled",
    ]
    database.close()
