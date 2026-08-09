"""Application-facing task lifecycle service with one in-process task provider."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from guarded_agent.agent import AgentLoop
from guarded_agent.config import load_settings
from guarded_agent.domain import FeedbackKind, Settings, TaskStatus
from guarded_agent.feedback import FeedbackEngine
from guarded_agent.governance import GovernanceEngine
from guarded_agent.providers.base import LLMProvider
from guarded_agent.redaction import Redactor
from guarded_agent.storage import Database, Task
from guarded_agent.subprocesses import CommandRunner
from guarded_agent.tools import ToolRegistry


class ApplicationService:
    """Create, execute, resume, and cancel one governed local task at a time."""

    def __init__(
        self,
        database: Database,
        *,
        configured_validation_commands: list[list[str]] | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.database = database
        self._configured_validation_commands = (
            [list(command) for command in configured_validation_commands]
            if configured_validation_commands is not None
            else None
        )
        self._redactor = redactor or Redactor([])
        self._providers: dict[str, LLMProvider] = {}

    def create(
        self,
        workspace: Path,
        goal: str,
        acceptance_commands: list[list[str]],
        provider: LLMProvider,
    ) -> Task:
        canonical_workspace = workspace.resolve(strict=True)
        if not canonical_workspace.is_dir():
            raise ValueError("workspace must be a directory")
        settings = load_settings(canonical_workspace)
        configured = self._configured_validation_commands
        if configured is None:
            configured = [list(command) for command in settings.validation_commands]
        configured_snapshot = frozenset(tuple(command) for command in configured)
        if any(tuple(command) not in configured_snapshot for command in acceptance_commands):
            raise ValueError("acceptance command was not configured at startup")
        settings = settings.model_copy(update={"validation_commands": configured})
        registered = self.database.tasks.get_workspace(str(canonical_workspace))
        if registered is None:
            registered = self.database.tasks.create_workspace(
                str(canonical_workspace), canonical_workspace.name
            )
        task = self.database.tasks.create_task(
            task_id=str(uuid4()),
            workspace_id=registered.id,
            goal=goal,
            acceptance_commands=[list(command) for command in acceptance_commands],
            limits=settings.model_dump(mode="json"),
        )
        self._providers[task.id] = provider
        return task

    def run(self, task_id: str) -> TaskStatus:
        loop = self._loop(task_id)
        try:
            return loop.run()
        finally:
            loop.tools.close()

    def resume(self, task_id: str, approval_id: str) -> TaskStatus:
        loop = self._loop(task_id)
        try:
            return loop.resume(approval_id)
        finally:
            loop.tools.close()

    def cancel(self, task_id: str) -> TaskStatus:
        task = self.database.tasks.get(task_id)
        if task.status is TaskStatus.CREATED:
            self.database.tasks.transition_status(
                task_id, TaskStatus.RUNNING, event_type="task_started", payload={"reason": "cancel"}
            )
            task = self.database.tasks.get(task_id)
        if task.status in {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}:
            self.database.tasks.transition_status(
                task_id, TaskStatus.CANCELLED, event_type="task_cancelled", payload={"reason": "user"}
            )
        return self.database.tasks.get(task_id).status

    def reject_approval(self, task_id: str, approval_id: str) -> TaskStatus:
        """Resume a paused task with explicit rejection feedback and no side effect."""
        task = self.database.tasks.get(task_id)
        if task.status is not TaskStatus.WAITING_APPROVAL:
            return task.status
        self.database.tasks.transition_status(
            task_id,
            TaskStatus.RUNNING,
            event_type="approval_rejected",
            payload={"approval_id": approval_id},
        )
        self.database.tasks.delete_pending_action(approval_id)
        self.database.tasks.add_turn(
            task_id,
            len(self.database.tasks.list_turns(task_id)) + 1,
            {},
            {
                "kind": FeedbackKind.POLICY_VIOLATION.value,
                "message": "approval was rejected by the user",
                "command_result": None,
            },
        )
        return self.database.tasks.get(task_id).status

    def pending_approval_id(self, task_id: str) -> str:
        with self.database.operation() as connection:
            row = connection.execute(
                "SELECT approval_id FROM pending_actions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"no pending approval for task: {task_id}")
        return str(row["approval_id"])

    def _loop(self, task_id: str) -> AgentLoop:
        task = self.database.tasks.get(task_id)
        provider = self._providers.get(task_id)
        if provider is None:
            raise ValueError("provider is unavailable for task")
        with self.database.operation() as connection:
            row = connection.execute(
                "SELECT canonical_path FROM workspaces WHERE id = ?", (task.workspace_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("task workspace is missing")
        workspace = Path(str(row["canonical_path"])).resolve(strict=True)
        settings = Settings.model_validate(task.limits)
        runner = CommandRunner(redactor=self._redactor, max_output_bytes=settings.max_output_bytes)
        return AgentLoop(
            database=self.database,
            task_id=task_id,
            workspace=workspace,
            settings=settings,
            provider=provider,
            tools=ToolRegistry(workspace, settings, self._redactor, runner=runner, memory_store=self.database.memory, workspace_id=task.workspace_id),
            feedback=FeedbackEngine(runner, configured_commands=settings.validation_commands),
            governance=GovernanceEngine(
                workspace=workspace, settings=settings, database=self.database, task_id=task_id
            ),
        )
