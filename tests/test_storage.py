from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from guarded_agent.domain import TaskStatus
from guarded_agent.storage import Database, InvalidTaskTransitionError


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


def test_pending_approval_defaults_to_a_ten_minute_aware_expiry(db: Database) -> None:
    """Catch approvals without an expiry that can be approved indefinitely."""
    before = datetime.now(UTC)
    approval = db.approvals.create_pending(
        task_id="t1", action_digest="abc", policy_version="1.0", summary="delete old.py"
    )
    after = datetime.now(UTC)

    assert approval.expires_at is not None
    assert approval.expires_at.tzinfo is not None
    assert before + timedelta(minutes=10) <= approval.expires_at <= after + timedelta(minutes=10)


def test_pending_approval_rejects_a_naive_expiry(db: Database) -> None:
    """Catch local-time expiry values whose ordering is ambiguous across deployments."""
    with pytest.raises(ValueError, match="timezone-aware"):
        db.approvals.create_pending(
            task_id="t1",
            action_digest="abc",
            policy_version="1.0",
            summary="delete old.py",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
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


def test_task_status_transition_enforces_the_state_graph(db: Database) -> None:
    """Catch a task store that permits a terminal or skipped lifecycle state."""
    with pytest.raises(InvalidTaskTransitionError, match="cannot transition task"):
        db.tasks.transition_status(
            "t1", TaskStatus.COMPLETED, event_type="task_completed", payload={}
        )

    db.tasks.transition_status("t1", TaskStatus.RUNNING, event_type="task_started", payload={})
    db.tasks.transition_status("t1", TaskStatus.COMPLETED, event_type="task_completed", payload={})

    with pytest.raises(InvalidTaskTransitionError, match="cannot transition task"):
        db.tasks.transition_status("t1", TaskStatus.RUNNING, event_type="resumed", payload={})


@pytest.mark.parametrize(
    "target", [TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED, TaskStatus.CANCELLED]
)
def test_created_task_cannot_skip_directly_to_a_terminal_state(
    db: Database, target: TaskStatus
) -> None:
    """Catch a created task bypassing the required RUNNING lifecycle state."""
    with pytest.raises(InvalidTaskTransitionError, match="cannot transition task"):
        db.tasks.transition_status("t1", target, event_type="task_stopped", payload={})

    assert db.tasks.get("t1").status is TaskStatus.CREATED
    assert db.audit.list_for_task("t1") == []


def test_failed_audit_insert_rolls_back_its_task_transition(tmp_path: Path) -> None:
    """Catch a task state commit that survives failure to append its audit event."""
    path = tmp_path / "guarded-agent.sqlite3"
    db = Database.open(path)
    workspace = db.tasks.create_workspace("/work/project", "project")
    db.tasks.create_task(
        task_id="t1", workspace_id=workspace.id, goal="Run validation", acceptance_commands=[], limits={}
    )
    db.connection.execute(
        """CREATE TRIGGER reject_started_audit BEFORE INSERT ON audit_events
           WHEN NEW.event_type = 'task_started'
           BEGIN SELECT RAISE(ABORT, 'audit blocked'); END;"""
    )

    with pytest.raises(Exception, match="audit blocked"):
        db.tasks.transition_status("t1", TaskStatus.RUNNING, event_type="task_started", payload={})
    db.close()

    reopened = Database.open(path)
    assert reopened.tasks.get("t1").status is TaskStatus.CREATED
    assert reopened.audit.list_for_task("t1") == []
    reopened.close()


def test_two_connections_can_consume_an_approval_only_once(tmp_path: Path) -> None:
    """Catch a check-then-update race that authorizes the same approval twice."""
    path = tmp_path / "guarded-agent.sqlite3"
    db1 = Database.open(path)
    workspace = db1.tasks.create_workspace("/work/project", "project")
    db1.tasks.create_task(
        task_id="t1", workspace_id=workspace.id, goal="Delete old.py", acceptance_commands=[], limits={}
    )
    now = datetime.now(UTC)
    approval = db1.approvals.create_pending(
        task_id="t1", action_digest="abc", policy_version="1.0", summary="delete old.py"
    )
    db1.approvals.approve(approval.id, now)
    db2 = Database.open(path)
    barrier = Barrier(2)
    results: list[bool] = []

    def consume(database: Database) -> None:
        barrier.wait()
        results.append(database.approvals.consume_if_authorized(approval.id, "abc", now))

    first = Thread(target=consume, args=(db1,))
    second = Thread(target=consume, args=(db2,))
    first.start()
    second.start()
    first.join()
    second.join()
    db1.close()
    db2.close()

    assert sorted(results) == [False, True]


def test_one_database_connection_is_safe_to_use_from_a_background_thread(db: Database) -> None:
    """Catch the default SQLite thread affinity error in WebUI background work."""
    def transition() -> None:
        db.tasks.transition_status("t1", TaskStatus.RUNNING, event_type="task_started", payload={})

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(transition).result()

    assert db.tasks.get("t1").status is TaskStatus.RUNNING


def test_turn_numbers_are_unique_per_task(db: Database) -> None:
    """Catch turns that can overwrite or duplicate one another within a task."""
    db.tasks.add_turn("t1", 1, {"tool": "read_file"}, {"kind": "PASS"})

    with pytest.raises(ValueError, match="turn"):
        db.tasks.add_turn("t1", 1, {"tool": "read_file"}, {"kind": "PASS"})
