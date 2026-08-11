"""SQLite-backed persistence for task execution and governance records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Self, cast
from uuid import uuid4

from pydantic import JsonValue

from guarded_agent.domain import TaskStatus


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class InvalidTaskTransitionError(ValueError):
    """Raised when a requested task lifecycle transition is not permitted."""


_ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.WAITING_APPROVAL, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    canonical_path: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    workspace_id: str
    goal: str
    status: TaskStatus
    acceptance_commands: list[list[str]]
    limits: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentTurn:
    id: str
    task_id: str
    turn_no: int
    action_json: dict[str, JsonValue]
    feedback_json: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    task_id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PendingAction:
    approval_id: str
    task_id: str
    action_json: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    task_id: str
    action_digest: str
    policy_version: str
    summary: str
    status: ApprovalStatus
    expires_at: datetime | None
    approved_at: datetime | None
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    task_id: str | None
    event_type: str
    redacted_payload: dict[str, JsonValue]
    previous_digest: str | None
    created_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_object(value: str) -> dict[str, JsonValue]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise TypeError("stored JSON object is malformed")
    return cast(dict[str, JsonValue], decoded)


class Database:
    """Owns one SQLite connection and its repositories."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = RLock()
        self.tasks = TaskStore(self)
        self.conversations = ConversationStore(self)
        self.audit = AuditStore(self)
        self.approvals = ApprovalStore(self)
        from guarded_agent.memory import MemoryStore

        self.memory = MemoryStore(self)

    @classmethod
    def open(cls, path: str | Path) -> Database:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        return cls(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for repositories that share this database."""
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @contextmanager
    def operation(self) -> Iterator[sqlite3.Connection]:
        """Serialize a single connection operation outside an explicit transaction."""
        with self._lock:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            canonical_path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            goal TEXT NOT NULL,
            status TEXT NOT NULL,
            acceptance_commands TEXT NOT NULL,
            limits TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_turns (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            turn_no INTEGER NOT NULL,
            action_json TEXT NOT NULL,
            feedback_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, turn_no)
        );
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES agent_turns(id),
            tool TEXT NOT NULL,
            normalized_args TEXT NOT NULL,
            result TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            action_digest TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CONSUMED')),
            expires_at TEXT,
            approved_at TEXT,
            consumed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_entries (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            trust TEXT NOT NULL,
            keywords TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(id),
            event_type TEXT NOT NULL,
            redacted_payload TEXT NOT NULL,
            previous_digest TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_configs (
            workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
            config_digest TEXT NOT NULL,
            parsed_values TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_actions (
            approval_id TEXT PRIMARY KEY REFERENCES approvals(id),
            task_id TEXT NOT NULL REFERENCES tasks(id),
            action_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


class TaskStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_workspace(self, canonical_path: str, name: str, *, workspace_id: str | None = None) -> Workspace:
        created_at = _now()
        workspace = Workspace(workspace_id or str(uuid4()), canonical_path, name, created_at)
        try:
            with self._database.operation() as connection:
                connection.execute(
                    "INSERT INTO workspaces VALUES (?, ?, ?, ?)",
                    (workspace.id, workspace.canonical_path, workspace.name, _timestamp(workspace.created_at)),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("workspace already exists") from error
        return workspace

    def get_workspace(self, canonical_path: str) -> Workspace | None:
        with self._database.operation() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE canonical_path = ?", (canonical_path,)
            ).fetchone()
        return _workspace_from_row(row) if row is not None else None

    def create_task(
        self,
        *,
        task_id: str,
        workspace_id: str,
        goal: str,
        acceptance_commands: list[list[str]],
        limits: dict[str, JsonValue],
    ) -> Task:
        created_at = _now()
        task = Task(
            task_id,
            workspace_id,
            goal,
            TaskStatus.CREATED,
            acceptance_commands,
            limits,
            created_at,
            created_at,
        )
        try:
            with self._database.operation() as connection:
                connection.execute(
                    """INSERT INTO tasks
                       (id, workspace_id, goal, status, acceptance_commands, limits, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task.id,
                        task.workspace_id,
                        task.goal,
                        task.status.value,
                        _json(task.acceptance_commands),
                        _json(task.limits),
                        _timestamp(task.created_at),
                        _timestamp(task.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("task or workspace does not exist") from error
        return task

    def get(self, task_id: str) -> Task:
        with self._database.operation() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return _task_from_row(row)

    def list_for_workspace(self, workspace_id: str) -> list[Task]:
        """Return a workspace's tasks newest first for a local control surface."""
        with self._database.operation() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE workspace_id = ? ORDER BY created_at DESC", (workspace_id,)
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def transition_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        event_type: str,
        payload: dict[str, JsonValue],
        previous_digest: str | None = None,
    ) -> Task:
        updated_at = _now()
        with self._database.transaction() as connection:
            row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"task not found: {task_id}")
            current_status = TaskStatus(cast(str, row["status"]))
            if status not in _ALLOWED_TASK_TRANSITIONS[current_status]:
                raise InvalidTaskTransitionError(
                    f"cannot transition task from {current_status.value} to {status.value}"
                )
            changed = connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (status.value, _timestamp(updated_at), task_id, current_status.value),
            ).rowcount
            if changed != 1:
                raise InvalidTaskTransitionError(
                    f"cannot transition task from {current_status.value} to {status.value}"
                )
            self._database.audit._append(
                connection, task_id, event_type, payload, previous_digest, updated_at
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise RuntimeError("updated task disappeared")
        return _task_from_row(row)

    def add_turn(
        self,
        task_id: str,
        turn_no: int,
        action_json: dict[str, JsonValue],
        feedback_json: dict[str, JsonValue],
    ) -> str:
        turn_id = str(uuid4())
        try:
            with self._database.operation() as connection:
                connection.execute(
                    """INSERT INTO agent_turns VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        turn_id,
                        task_id,
                        turn_no,
                        _json(action_json),
                        _json(feedback_json),
                        _timestamp(_now()),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("task does not exist or turn number is already recorded") from error
        return turn_id

    def add_tool_execution(
        self,
        turn_id: str,
        tool: str,
        normalized_args: dict[str, JsonValue],
        result: dict[str, JsonValue],
        duration_ms: int,
    ) -> str:
        execution_id = str(uuid4())
        try:
            with self._database.operation() as connection:
                connection.execute(
                    "INSERT INTO tool_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        execution_id,
                        turn_id,
                        tool,
                        _json(normalized_args),
                        _json(result),
                        duration_ms,
                        _timestamp(_now()),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("turn does not exist") from error
        return execution_id

    def list_turns(self, task_id: str, *, limit: int | None = None) -> list[AgentTurn]:
        query = "SELECT * FROM agent_turns WHERE task_id = ? ORDER BY turn_no DESC"
        parameters: tuple[object, ...] = (task_id,)
        if limit is not None:
            query += " LIMIT ?"
            parameters = (task_id, limit)
        with self._database.operation() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return list(reversed([_turn_from_row(row) for row in rows]))

    def save_pending_action(
        self, *, approval_id: str, task_id: str, action_json: dict[str, JsonValue]
    ) -> PendingAction:
        pending = PendingAction(approval_id, task_id, action_json, _now())
        try:
            with self._database.operation() as connection:
                connection.execute(
                    "INSERT INTO pending_actions VALUES (?, ?, ?, ?)",
                    (
                        pending.approval_id,
                        pending.task_id,
                        _json(pending.action_json),
                        _timestamp(pending.created_at),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("approval or task does not exist, or pending action already exists") from error
        return pending

    def get_pending_action(self, approval_id: str) -> PendingAction:
        with self._database.operation() as connection:
            row = connection.execute(
                "SELECT * FROM pending_actions WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"pending action not found: {approval_id}")
        return _pending_action_from_row(row)

    def delete_pending_action(self, approval_id: str) -> None:
        with self._database.operation() as connection:
            connection.execute("DELETE FROM pending_actions WHERE approval_id = ?", (approval_id,))


class ConversationStore:
    """Persist bounded user/agent transcript entries for one governed task."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, task_id: str, role: str, content: str) -> ConversationMessage:
        if role not in {"user", "agent"}:
            raise ValueError("conversation role must be user or agent")
        if not content or len(content) > 4096:
            raise ValueError("conversation content must be between 1 and 4096 characters")
        message = ConversationMessage(str(uuid4()), task_id, role, content, _now())
        try:
            with self._database.operation() as connection:
                connection.execute(
                    "INSERT INTO conversation_messages VALUES (?, ?, ?, ?, ?)",
                    (message.id, message.task_id, message.role, message.content, _timestamp(message.created_at)),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("task does not exist") from error
        return message

    def list_for_task(self, task_id: str) -> list[ConversationMessage]:
        with self._database.operation() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE task_id = ? ORDER BY created_at, id",
                (task_id,),
            ).fetchall()
        return [_conversation_message_from_row(row) for row in rows]


class AuditStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def append(
        self,
        task_id: str | None,
        event_type: str,
        redacted_payload: dict[str, JsonValue],
        previous_digest: str | None = None,
    ) -> AuditEvent:
        with self._database.transaction() as connection:
            return self._append(
                connection, task_id, event_type, redacted_payload, previous_digest, _now()
            )

    def _append(
        self,
        connection: sqlite3.Connection,
        task_id: str | None,
        event_type: str,
        redacted_payload: dict[str, JsonValue],
        previous_digest: str | None,
        created_at: datetime,
    ) -> AuditEvent:
        event = AuditEvent(
            str(uuid4()), task_id, event_type, redacted_payload, previous_digest, created_at
        )
        try:
            connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.task_id,
                    event.event_type,
                    _json(event.redacted_payload),
                    event.previous_digest,
                    _timestamp(event.created_at),
                ),
            )
        except sqlite3.IntegrityError as error:
            if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
                raise ValueError("task does not exist") from error
            raise
        return event

    def list_for_task(self, task_id: str) -> list[AuditEvent]:
        with self._database.operation() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE task_id = ? ORDER BY created_at, id", (task_id,)
            ).fetchall()
        return [_audit_from_row(row) for row in rows]


class ApprovalStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_pending(
        self,
        *,
        task_id: str,
        action_digest: str,
        policy_version: str,
        summary: str,
        expires_at: datetime | None = None,
    ) -> Approval:
        created_at = _now()
        expiry = expires_at or created_at + timedelta(minutes=10)
        expiry_value = _timestamp(expiry)
        approval = Approval(
            str(uuid4()),
            task_id,
            action_digest,
            policy_version,
            summary,
            ApprovalStatus.PENDING,
            expiry,
            None,
            None,
        )
        with self._database.transaction() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise ValueError(f"task does not exist: {task_id}")
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.id,
                    approval.task_id,
                    approval.action_digest,
                    approval.policy_version,
                    approval.summary,
                    approval.status.value,
                    expiry_value,
                    None,
                    None,
                ),
            )
        return approval

    def get(self, approval_id: str) -> Approval:
        with self._database.operation() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return _approval_from_row(row)

    def approve(self, approval_id: str, now: datetime) -> Approval:
        now_value = _timestamp(now)
        with self._database.transaction() as connection:
            connection.execute(
                """UPDATE approvals
                   SET status = CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 'EXPIRED'
                                     ELSE 'APPROVED' END,
                       approved_at = CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN NULL
                                          ELSE ? END
                   WHERE id = ? AND status = 'PENDING'""",
                (now_value, now_value, now_value, approval_id),
            )
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return _approval_from_row(row)

    def reject(self, approval_id: str, now: datetime) -> Approval:
        now_value = _timestamp(now)
        with self._database.transaction() as connection:
            connection.execute(
                """UPDATE approvals
                   SET status = CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 'EXPIRED'
                                     ELSE 'REJECTED' END
                   WHERE id = ? AND status = 'PENDING'""",
                (now_value, approval_id),
            )
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"approval not found: {approval_id}")
        return _approval_from_row(row)

    def consume_if_authorized(
        self,
        approval_id: str,
        *,
        expected_digest: str,
        expected_task_id: str,
        expected_policy_version: str,
        now: datetime,
    ) -> bool:
        now_value = _timestamp(now)
        with self._database.transaction() as connection:
            consumed = connection.execute(
                """UPDATE approvals SET status = 'CONSUMED', consumed_at = ?
                   WHERE id = ? AND action_digest = ? AND task_id = ? AND policy_version = ?
                     AND status = 'APPROVED'
                     AND (expires_at IS NULL OR expires_at > ?)""",
                (
                    now_value,
                    approval_id,
                    expected_digest,
                    expected_task_id,
                    expected_policy_version,
                    now_value,
                ),
            ).rowcount
        return consumed == 1


def _task_from_row(row: sqlite3.Row) -> Task:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    updated_at = _parse_timestamp(cast(str, row["updated_at"]))
    if created_at is None or updated_at is None:
        raise ValueError("task timestamps are malformed")
    commands = cast(list[list[str]], json.loads(cast(str, row["acceptance_commands"])))
    return Task(
        cast(str, row["id"]),
        cast(str, row["workspace_id"]),
        cast(str, row["goal"]),
        TaskStatus(cast(str, row["status"])),
        commands,
        _json_object(cast(str, row["limits"])),
        created_at,
        updated_at,
    )


def _workspace_from_row(row: sqlite3.Row) -> Workspace:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    if created_at is None:
        raise ValueError("workspace timestamp is malformed")
    return Workspace(
        cast(str, row["id"]),
        cast(str, row["canonical_path"]),
        cast(str, row["name"]),
        created_at,
    )


def _turn_from_row(row: sqlite3.Row) -> AgentTurn:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    if created_at is None:
        raise ValueError("turn timestamp is malformed")
    return AgentTurn(
        cast(str, row["id"]),
        cast(str, row["task_id"]),
        cast(int, row["turn_no"]),
        _json_object(cast(str, row["action_json"])),
        _json_object(cast(str, row["feedback_json"])),
        created_at,
    )


def _conversation_message_from_row(row: sqlite3.Row) -> ConversationMessage:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    if created_at is None:
        raise ValueError("conversation timestamp is malformed")
    return ConversationMessage(
        cast(str, row["id"]),
        cast(str, row["task_id"]),
        cast(str, row["role"]),
        cast(str, row["content"]),
        created_at,
    )


def _pending_action_from_row(row: sqlite3.Row) -> PendingAction:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    if created_at is None:
        raise ValueError("pending action timestamp is malformed")
    return PendingAction(
        cast(str, row["approval_id"]),
        cast(str, row["task_id"]),
        _json_object(cast(str, row["action_json"])),
        created_at,
    )


def _approval_from_row(row: sqlite3.Row) -> Approval:
    return Approval(
        cast(str, row["id"]),
        cast(str, row["task_id"]),
        cast(str, row["action_digest"]),
        cast(str, row["policy_version"]),
        cast(str, row["summary"]),
        ApprovalStatus(cast(str, row["status"])),
        _parse_timestamp(cast(str | None, row["expires_at"])),
        _parse_timestamp(cast(str | None, row["approved_at"])),
        _parse_timestamp(cast(str | None, row["consumed_at"])),
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    created_at = _parse_timestamp(cast(str, row["created_at"]))
    if created_at is None:
        raise ValueError("audit timestamp is malformed")
    return AuditEvent(
        cast(str, row["id"]),
        cast(str | None, row["task_id"]),
        cast(str, row["event_type"]),
        _json_object(cast(str, row["redacted_payload"])),
        cast(str | None, row["previous_digest"]),
        created_at,
    )
