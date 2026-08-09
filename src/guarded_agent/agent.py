"""The deterministic, single-task governed agent state machine."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue, ValidationError

from guarded_agent.context import ContextBuilder
from guarded_agent.domain import (
    Feedback,
    FeedbackKind,
    GovernanceOutcome,
    Settings,
    TaskStatus,
    ToolAction,
    ToolName,
    ToolResult,
    parse_tool_action,
)
from guarded_agent.feedback import FeedbackEngine
from guarded_agent.governance import GovernanceEngine
from guarded_agent.providers.base import LLMProvider, ProviderResponseError
from guarded_agent.storage import Database, Task
from guarded_agent.tools import ToolRegistry

_MUTATING_TOOLS = {
    ToolName.WRITE_FILE,
    ToolName.DELETE_FILE,
    ToolName.MOVE_FILE,
    ToolName.RUN_COMMAND,
}
_FAILURE_KINDS = {
    FeedbackKind.TEST_FAILURE,
    FeedbackKind.TOOL_FAILURE,
    FeedbackKind.TIMEOUT,
    FeedbackKind.INVALID_ACTION,
    FeedbackKind.POLICY_VIOLATION,
}


class AgentLoop:
    """Run one task through provider, governance, tools, and objective feedback."""

    def __init__(
        self,
        *,
        database: Database,
        task_id: str,
        workspace: Path,
        settings: Settings,
        provider: LLMProvider,
        tools: ToolRegistry,
        feedback: FeedbackEngine,
        governance: GovernanceEngine,
    ) -> None:
        self.database = database
        self.task_id = task_id
        self.workspace = governance.workspace
        self.settings = settings.model_copy(deep=True)
        self.provider = provider
        self.tools = tools
        self.feedback = feedback
        self.governance = governance
        self.context = ContextBuilder(database.memory)

    def step(self, task_id: str | None = None) -> TaskStatus:
        """Advance this loop's task once; an explicit id must match its binding."""
        if task_id is not None and task_id != self.task_id:
            raise ValueError("loop is bound to a different task")
        task = self.database.tasks.get(self.task_id)
        if task.status is TaskStatus.CREATED:
            task = self._transition(TaskStatus.RUNNING, "task_started", {"reason": "loop step"})
        if task.status is not TaskStatus.RUNNING:
            return task.status
        if self._timed_out(task):
            return self._fail("task_timed_out", "total task timeout exceeded")
        turns = self.database.tasks.list_turns(task.id)
        if len(turns) >= self.settings.max_turns:
            return self._fail("turn_limit_exceeded", "maximum turn count reached")

        turn_number = len(turns) + 1
        try:
            raw_action = cast(object, self.provider.next_action(self.context.build(task, turns[-8:])))
            action = _strict_action(raw_action)
        except (ProviderResponseError, ValidationError, TypeError, ValueError) as error:
            return self._record_feedback(
                turn_number,
                {},
                _feedback(FeedbackKind.INVALID_ACTION, f"invalid provider action: {error}"),
            )
        except Exception as error:  # noqa: BLE001
            return self._record_feedback(
                turn_number,
                {},
                _feedback(FeedbackKind.INVALID_ACTION, f"provider failure: {type(error).__name__}"),
            )

        decision = self.governance.evaluate(action)
        action_json = _action_json(action)
        self.database.audit.append(
            task.id,
            "governance_decision",
            {
                "tool": action.tool.value,
                "outcome": decision.outcome.value,
                "rule_id": decision.rule_id,
            },
            decision.action_digest,
        )
        if decision.outcome is GovernanceOutcome.DENY:
            return self._record_feedback(
                turn_number,
                action_json,
                _feedback(FeedbackKind.POLICY_VIOLATION, decision.reason),
            )
        if decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL:
            approval = self.governance.create_pending_approval(task.id, action)
            self.database.tasks.save_pending_action(
                approval_id=approval.id, task_id=task.id, action_json=action_json
            )
            self._add_turn(
                turn_number,
                action_json,
                _feedback(FeedbackKind.PASS, "action is waiting for approval"),
            )
            self._transition(
                TaskStatus.WAITING_APPROVAL,
                "approval_requested",
                {"approval_id": approval.id, "rule_id": decision.rule_id},
                decision.action_digest,
            )
            return TaskStatus.WAITING_APPROVAL
        return self._execute_action(turn_number, action, action_json)

    def run(self) -> TaskStatus:
        while True:
            status = self.step()
            if status is not TaskStatus.RUNNING:
                return status

    def resume(self, approval_id: str) -> TaskStatus:
        task = self.database.tasks.get(self.task_id)
        if task.status is not TaskStatus.WAITING_APPROVAL:
            return task.status
        if self._timed_out(task) or len(self.database.tasks.list_turns(task.id)) >= self.settings.max_turns:
            return task.status
        try:
            pending = self.database.tasks.get_pending_action(approval_id)
            if pending.task_id != task.id:
                raise ValueError("approval does not belong to task")
            action = parse_tool_action(pending.action_json)
        except (KeyError, ValidationError, ValueError) as error:
            self._transition(
                TaskStatus.RUNNING,
                "approval_mismatch",
                {"approval_id": approval_id, "reason": str(error)},
            )
            return self._record_feedback(
                len(self.database.tasks.list_turns(task.id)) + 1,
                {},
                _feedback(FeedbackKind.POLICY_VIOLATION, "persisted approval action is invalid"),
            )

        self._transition(TaskStatus.RUNNING, "approval_resume_started", {"approval_id": approval_id})
        if not self.governance.authorize_persisted(task.id, action, approval_id, datetime.now(UTC)):
            return self._record_feedback(
                len(self.database.tasks.list_turns(task.id)) + 1,
                _action_json(action),
                _feedback(FeedbackKind.POLICY_VIOLATION, "approval no longer authorizes this action"),
            )
        self.database.tasks.delete_pending_action(approval_id)
        return self._execute_action(
            len(self.database.tasks.list_turns(task.id)) + 1, action, _action_json(action)
        )

    def _execute_action(
        self, turn_number: int, action: ToolAction, action_json: dict[str, JsonValue]
    ) -> TaskStatus:
        result = self.tools.execute(action)
        if action.tool is ToolName.CANNOT_CONTINUE:
            feedback = _feedback(FeedbackKind.TOOL_FAILURE, action.arguments.reason, can_continue=False)
            self._add_turn(turn_number, action_json, feedback, result)
            return self._fail("agent_cannot_continue", action.arguments.reason)
        if action.tool is ToolName.COMPLETE:
            feedback = self._verify()
            self._add_turn(turn_number, action_json, feedback, result)
            if feedback.kind is FeedbackKind.PASS:
                self._transition(
                    TaskStatus.COMPLETED,
                    "task_completed",
                    {"summary": action.arguments.summary},
                )
                return TaskStatus.COMPLETED
            return self._after_feedback(feedback)
        if result.exit_code not in {0, None} or result.exit_code is None:
            feedback = _feedback(FeedbackKind.TOOL_FAILURE, "tool execution failed", result)
        elif action.tool in _MUTATING_TOOLS:
            feedback = self._verify()
        else:
            feedback = _feedback(FeedbackKind.PASS, "tool action completed", result)
        self._add_turn(turn_number, action_json, feedback, result)
        return self._after_feedback(feedback)

    def _verify(self) -> Feedback:
        task = self.database.tasks.get(self.task_id)
        return self.feedback.verify(
            task.acceptance_commands,
            self.workspace,
            timeout=self.settings.command_timeout_seconds,
        )

    def _record_feedback(
        self,
        turn_number: int,
        action_json: dict[str, JsonValue],
        feedback: Feedback,
    ) -> TaskStatus:
        self._add_turn(turn_number, action_json, feedback)
        return self._after_feedback(feedback)

    def _add_turn(
        self,
        turn_number: int,
        action_json: dict[str, JsonValue],
        feedback: Feedback,
        result: ToolResult | None = None,
    ) -> None:
        turn_id = self.database.tasks.add_turn(
            self.task_id,
            turn_number,
            action_json,
            cast(dict[str, JsonValue], feedback.model_dump(mode="json")),
        )
        if result is not None:
            self.database.tasks.add_tool_execution(
                turn_id,
                result.tool.value,
                cast(dict[str, JsonValue], action_json.get("arguments", {})),
                cast(dict[str, JsonValue], result.model_dump(mode="json")),
                result.duration_ms,
            )

    def _after_feedback(self, feedback: Feedback) -> TaskStatus:
        if feedback.kind in _FAILURE_KINDS and self._consecutive_failures() >= self.settings.max_consecutive_failures:
            return self._fail("consecutive_failure_limit", "maximum consecutive failures reached")
        return TaskStatus.RUNNING

    def _consecutive_failures(self) -> int:
        failures = 0
        for turn in reversed(self.database.tasks.list_turns(self.task_id)):
            kind = turn.feedback_json.get("kind")
            if kind not in {item.value for item in _FAILURE_KINDS}:
                break
            failures += 1
        return failures

    def _timed_out(self, task: Task) -> bool:
        return (datetime.now(UTC) - task.created_at).total_seconds() >= self.settings.total_timeout_seconds

    def _fail(self, event_type: str, reason: str) -> TaskStatus:
        self._transition(TaskStatus.FAILED, event_type, {"reason": reason})
        return TaskStatus.FAILED

    def _transition(
        self,
        status: TaskStatus,
        event_type: str,
        payload: dict[str, JsonValue],
        previous_digest: str | None = None,
    ) -> Task:
        return self.database.tasks.transition_status(
            self.task_id,
            status,
            event_type=event_type,
            payload=payload,
            previous_digest=previous_digest,
        )


def _strict_action(value: object) -> ToolAction:
    if isinstance(value, BaseModel):
        return parse_tool_action(value.model_dump(mode="python"))
    return parse_tool_action(value)


def _action_json(action: ToolAction) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], action.model_dump(mode="json"))


def _feedback(kind: FeedbackKind, message: str, result: ToolResult | None = None, *, can_continue: bool = True) -> Feedback:
    return Feedback(kind=kind, message=message, command_result=result, can_continue=can_continue)
