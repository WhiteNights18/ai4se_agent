from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from guarded_agent.domain import TaskStatus
from guarded_agent.storage import Database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.open(tmp_path / "guarded-agent.sqlite3")
    workspace = database.tasks.create_workspace("/work/project", "project")
    database.tasks.create_task(
        task_id="t1",
        workspace_id=workspace.id,
        goal="Safely remove obsolete code",
        acceptance_commands=[["pytest", "-q"]],
        limits={"max_turns": 2},
    )
    yield database
    database.close()


def test_approval_can_be_consumed_only_once(db: Database) -> None:
    """Catch a replayable approval that can authorize a destructive action twice."""
    now = datetime.now(UTC)
    approval = db.approvals.create_pending(
        task_id="t1",
        action_digest="abc",
        policy_version="1.0",
        summary="delete old.py",
        expires_at=now + timedelta(minutes=5),
    )

    db.approvals.approve(approval.id, now)

    assert db.approvals.consume_if_authorized(approval.id, "abc", now) is True
    assert db.approvals.consume_if_authorized(approval.id, "abc", now) is False


def test_approval_rejects_wrong_digest_and_expired_approval(db: Database) -> None:
    """Catch authorization that ignores a changed action or an expired approval."""
    now = datetime.now(UTC)
    approval = db.approvals.create_pending(
        task_id="t1",
        action_digest="original",
        policy_version="1.0",
        summary="delete old.py",
        expires_at=now + timedelta(minutes=1),
    )
    db.approvals.approve(approval.id, now)

    assert db.approvals.consume_if_authorized(approval.id, "tampered", now) is False
    assert db.approvals.consume_if_authorized(approval.id, "original", now + timedelta(minutes=2)) is False


def test_approval_requires_an_existing_task(db: Database) -> None:
    """Catch an approval store that permits an orphan approval record."""
    with pytest.raises(ValueError, match="task"):
        db.approvals.create_pending(
            task_id="missing",
            action_digest="abc",
            policy_version="1.0",
            summary="delete old.py",
        )


def test_task_transition_and_audit_event_persist_across_reopen(tmp_path: Path) -> None:
    """Catch task state and its audit trail being committed independently."""
    path = tmp_path / "guarded-agent.sqlite3"
    db = Database.open(path)
    workspace = db.tasks.create_workspace("/work/project", "project")
    db.tasks.create_task(
        task_id="t1",
        workspace_id=workspace.id,
        goal="Run validation",
        acceptance_commands=[],
        limits={},
    )
    db.tasks.transition_status(
        "t1",
        TaskStatus.RUNNING,
        event_type="task_started",
        payload={"reason": "worker claimed task"},
    )
    db.close()

    reopened = Database.open(path)
    assert reopened.tasks.get("t1").status is TaskStatus.RUNNING
    events = reopened.audit.list_for_task("t1")
    assert [(event.event_type, event.redacted_payload) for event in events] == [
        ("task_started", {"reason": "worker claimed task"})
    ]
    reopened.close()


def test_turn_numbers_are_unique_per_task(db: Database) -> None:
    """Catch turns that can overwrite or duplicate one another within a task."""
    db.tasks.add_turn("t1", 1, {"tool": "read_file"}, {"kind": "PASS"})

    with pytest.raises(ValueError, match="turn"):
        db.tasks.add_turn("t1", 1, {"tool": "read_file"}, {"kind": "PASS"})
