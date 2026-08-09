"""Deterministic classification of configured validation commands."""

from __future__ import annotations

from pathlib import Path

from guarded_agent.domain import Feedback, FeedbackKind, ToolName, ToolResult
from guarded_agent.subprocesses import CommandResult, CommandRunner, ProcessStatus


class FeedbackEngine:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def verify(
        self,
        commands: list[list[str]],
        cwd: Path,
        timeout: float = 120,
    ) -> Feedback:
        for argv in commands:
            result = self._runner.run(argv, cwd, timeout)
            tool_result = _tool_result(result)
            if result.status is ProcessStatus.TIMED_OUT:
                return Feedback(
                    kind=FeedbackKind.TIMEOUT,
                    message="validation command timed out",
                    command_result=tool_result,
                    can_continue=True,
                )
            if result.status is ProcessStatus.START_FAILED:
                return Feedback(
                    kind=FeedbackKind.TOOL_FAILURE,
                    message="validation command could not start",
                    command_result=tool_result,
                    can_continue=True,
                )
            if result.exit_code != 0:
                return Feedback(
                    kind=FeedbackKind.TEST_FAILURE,
                    message="validation command failed",
                    command_result=tool_result,
                    can_continue=True,
                )
        return Feedback(
            kind=FeedbackKind.PASS,
            message="all validation commands passed",
            command_result=None,
            can_continue=True,
        )


def _tool_result(result: CommandResult) -> ToolResult:
    return ToolResult(
        tool=ToolName.RUN_VALIDATOR,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
        duration_ms=result.duration_ms,
        changes=[],
    )
