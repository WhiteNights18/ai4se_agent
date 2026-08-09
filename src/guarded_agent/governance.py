"""Compiled governance policy and single-use approval binding."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from guarded_agent.domain import (
    Action,
    GovernanceDecision,
    GovernanceOutcome,
    Settings,
    TaskStatus,
    ToolName,
)
from guarded_agent.paths import (
    PolicyDenied,
    canonicalize_inside,
    is_sensitive_path,
    normalize_relative_posix,
)
from guarded_agent.storage import Approval, ApprovalStatus, ApprovalStore, AuditStore, Database


@dataclass(frozen=True, slots=True)
class Policy:
    """The repository-independent v1 policy shipped with the program."""

    version: str = "1.0"

    def __post_init__(self) -> None:
        if self.version != "1.0":
            raise ValueError("only compiled policy version 1.0 is available")


_READ_ONLY_TOOLS = {
    ToolName.LIST_DIRECTORY,
    ToolName.READ_FILE,
    ToolName.SEARCH_TEXT,
    ToolName.GIT_STATUS,
    ToolName.GIT_DIFF,
    ToolName.RETRIEVE_MEMORY,
    ToolName.COMPLETE,
    ToolName.CANNOT_CONTINUE,
}
_FILE_PATH_TOOLS = {
    ToolName.LIST_DIRECTORY,
    ToolName.READ_FILE,
    ToolName.SEARCH_TEXT,
    ToolName.WRITE_FILE,
    ToolName.DELETE_FILE,
}
_PATH_ARGUMENT_KEYS = {"path", "source", "destination", "source_path", "destination_path"}
_HARD_DENIED_EXECUTABLES = {
    "env",
    "bash",
    "sh",
    "zsh",
    "dash",
    "fish",
    "csh",
    "tcsh",
    "ksh",
    "ash",
    "sudo",
    "doas",
    "su",
    "pkexec",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
}
_SHELL_TOKENS = {">", ">>", "<", "<<", "|", "||", "&&", ";"}
_RG_SAFE_FLAGS = {"-n", "--line-number", "-i", "--ignore-case", "-F", "--fixed-strings"}
_RG_HARD_OPTIONS = {"--pre", "--pre-glob", "-f", "--file"}
_GIT_STATUS_SAFE_FLAGS = {
    "-s",
    "--short",
    "-b",
    "--branch",
    "--porcelain",
    "--porcelain=v1",
    "--porcelain=v2",
    "--long",
    "--no-renames",
    "--renames",
    "--ahead-behind",
    "--no-ahead-behind",
    "--untracked-files",
}
_GIT_DIFF_SAFE_FLAGS = {
    "--stat",
    "--numstat",
    "--shortstat",
    "--summary",
    "--name-only",
    "--name-status",
    "--check",
    "--cached",
    "--staged",
    "--no-renames",
    "--minimal",
    "--patience",
    "--histogram",
    "--binary",
    "--full-index",
    "--no-color",
}
_BUSYBOX_SHELL_APPLETS = {"sh", "ash", "bash", "dash", "hush"}


class _SensitivePathDenied(PolicyDenied):
    pass


class GovernanceEngine:
    """Evaluate actions in one canonical workspace and bind approvals to one task."""

    def __init__(
        self,
        *,
        workspace: Path,
        settings: Settings,
        database: Database,
        task_id: str,
    ) -> None:
        canonical_workspace = workspace.resolve(strict=True)
        if not canonical_workspace.is_dir():
            raise ValueError("workspace must be a directory")
        with database.operation() as connection:
            row = connection.execute(
                """SELECT workspaces.canonical_path
                   FROM tasks JOIN workspaces ON workspaces.id = tasks.workspace_id
                   WHERE tasks.id = ?""",
                (task_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"task does not exist: {task_id}")
        registered_workspace = Path(str(row["canonical_path"])).resolve(strict=True)
        if registered_workspace != canonical_workspace:
            raise ValueError("workspace does not match the task's registered workspace")

        self._workspace = canonical_workspace
        self.settings = settings.model_copy(deep=True)
        self._validation_commands = tuple(tuple(command) for command in settings.validation_commands)
        self.database = database
        self._task_id = task_id
        self.policy = Policy(version="1.0")
        self.approvals: ApprovalStore = database.approvals
        self.audit: AuditStore = database.audit

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def task_id(self) -> str:
        return self._task_id

    def canonical_targets(self, action: Action) -> tuple[Path, ...]:
        """Return policy-checked canonical targets for a file action.

        This is a governance snapshot, not an execution-time TOCTOU guarantee. Task 5 must
        consume these targets with a workspace-dirfd beneath/no-follow primitive and must not
        reopen the submitted path spelling after approval.
        """
        if action.tool not in _FILE_PATH_TOOLS and action.tool is not ToolName.MOVE_FILE:
            return ()
        return tuple(
            self._canonicalize_candidate_path(candidate)
            for candidate in _file_path_values(action)
        )

    def evaluate(self, action: Action) -> GovernanceDecision:
        """Run the complete policy pipeline and fail closed on evaluation errors."""
        try:
            return self._evaluate(action)
        except _SensitivePathDenied as error:
            return _decision(GovernanceOutcome.DENY, "sensitive_path", str(error))
        except PolicyDenied as error:
            return _decision(GovernanceOutcome.DENY, "workspace_boundary", str(error))
        # A policy engine must fail closed for any unexpected evaluation error.
        except Exception:  # noqa: BLE001
            return _decision(
                GovernanceOutcome.DENY,
                "policy_evaluation_failed",
                "the compiled policy could not evaluate this action",
            )

    def create_pending_approval(
        self,
        task_id: str,
        action: Action,
        *,
        expires_at: datetime | None = None,
    ) -> Approval:
        """Persist a pending record only for this task's approvable current action."""
        if task_id != self.task_id:
            raise PolicyDenied("approval task does not match the governance context")
        decision = self.evaluate(action)
        if (
            decision.outcome is not GovernanceOutcome.REQUIRE_APPROVAL
            or decision.action_digest is None
        ):
            raise PolicyDenied("action is not eligible for approval")
        return self.approvals.create_pending(
            task_id=task_id,
            action_digest=decision.action_digest,
            policy_version=self.policy.version,
            summary=f"{action.tool.value} requires approval",
            expires_at=expires_at,
        )

    def authorize_persisted(
        self,
        task_id: str,
        action: Action,
        approval_id: str,
        now: datetime,
    ) -> bool:
        """Re-evaluate a persisted action and atomically consume its exact approval once."""
        try:
            approval = self.approvals.get(approval_id)
        except KeyError:
            self._record_approval_mismatch(self.task_id, approval_id, "approval not found")
            return False

        decision = self.evaluate(action)
        expected_digest = decision.action_digest
        mismatch_reason: str | None = None
        if task_id != self.task_id or approval.task_id != task_id:
            mismatch_reason = "task binding changed"
        elif approval.policy_version != self.policy.version:
            mismatch_reason = "policy version changed"
        elif decision.outcome is not GovernanceOutcome.REQUIRE_APPROVAL:
            mismatch_reason = "action is no longer approvable"
        elif expected_digest is None or approval.action_digest != expected_digest:
            mismatch_reason = "action digest changed"
        elif approval.status is not ApprovalStatus.APPROVED:
            mismatch_reason = "approval is not approved"

        if mismatch_reason is None and expected_digest is not None:
            if self.approvals.consume_if_authorized(
                approval_id,
                expected_digest=expected_digest,
                expected_task_id=self.task_id,
                expected_policy_version=self.policy.version,
                now=now,
            ):
                return True
            mismatch_reason = "approval expired or was already consumed"

        self._record_approval_mismatch(self.task_id, approval_id, cast(str, mismatch_reason))
        return False

    def _evaluate(self, action: Action) -> GovernanceDecision:
        tool = action.tool
        if not isinstance(action.arguments, dict):
            raise TypeError("action arguments must be an object")

        if tool in _FILE_PATH_TOOLS or tool is ToolName.MOVE_FILE:
            self.canonical_targets(action)

        if tool in _READ_ONLY_TOOLS:
            return _decision(GovernanceOutcome.ALLOW, "read_only", "read-only action")

        if tool is ToolName.WRITE_FILE:
            content = action.arguments.get("content")
            if not isinstance(content, str):
                raise TypeError("write content must be text")
            byte_limit = min(self.settings.max_output_bytes, 65_536)
            if len(content.encode("utf-8")) > byte_limit:
                return _decision(
                    GovernanceOutcome.DENY,
                    "resource_limit_exceeded",
                    "write content exceeds the configured byte limit",
                )
            return _decision(GovernanceOutcome.ALLOW, "bounded_write", "bounded workspace write")

        if tool in {ToolName.DELETE_FILE, ToolName.MOVE_FILE}:
            return self._approval_decision(
                action, "destructive_file_change", "destructive file change requires approval"
            )

        if tool is ToolName.SAVE_MEMORY:
            _require_non_empty_text(action.arguments, "content")
            _require_non_empty_text(action.arguments, "category")
            return self._approval_decision(
                action, "memory_persistence", "persisting trusted memory requires approval"
            )

        if tool is ToolName.RUN_VALIDATOR:
            argv = _validated_argv(action.arguments)
            if _is_hard_denied(argv):
                return _decision(
                    GovernanceOutcome.DENY,
                    "hard_denied_command",
                    "configured validators cannot override hard command denials",
                )
            if tuple(argv) in self._validation_commands:
                for candidate in _command_path_candidates(argv):
                    self._enforce_candidate_path(candidate)
                return _decision(
                    GovernanceOutcome.ALLOW,
                    "configured_validator",
                    "argv exactly matches a startup-configured validator",
                )
            return _decision(
                GovernanceOutcome.DENY,
                "validator_not_configured",
                "validator argv was not pre-authorized at startup",
            )

        if tool is ToolName.RUN_COMMAND:
            return self._evaluate_command(action)

        return _decision(GovernanceOutcome.DENY, "unknown_tool", "tool is not in policy version 1.0")

    def _canonicalize_candidate_path(self, candidate: str) -> Path:
        normalized = normalize_relative_posix(candidate)
        resolved = canonicalize_inside(self.workspace, normalized)
        resolved_relative = resolved.relative_to(self.workspace).as_posix()
        if is_sensitive_path(normalized) or is_sensitive_path(resolved_relative):
            raise _SensitivePathDenied("sensitive paths are unavailable to ordinary tools")
        return resolved

    def _enforce_candidate_path(self, candidate: str) -> None:
        self._canonicalize_candidate_path(candidate)

    def _evaluate_command(self, action: Action) -> GovernanceDecision:
        argv = _validated_argv(action.arguments)
        if _is_hard_denied(argv):
            return _decision(
                GovernanceOutcome.DENY,
                "hard_denied_command",
                "command is a non-approvable hard denial",
            )

        if not _contains_shell_syntax(argv):
            read_paths = _approved_read_command_paths(argv)
            if read_paths is not None:
                for candidate in read_paths:
                    self._enforce_candidate_path(candidate)
                if argv[0:2] == ["git", "status"]:
                    return _decision(
                        GovernanceOutcome.ALLOW,
                        "approved_read_command",
                        "command matches the narrow read-only argv policy",
                    )

        return self._approval_decision(
            action, "command_requires_approval", "command argv requires approval"
        )

    def _approval_decision(self, action: Action, rule_id: str, reason: str) -> GovernanceDecision:
        digest = action_digest(
            task_id=self.task_id,
            action=action,
            workspace=self.workspace,
            policy_version=self.policy.version,
        )
        return GovernanceDecision(
            outcome=GovernanceOutcome.REQUIRE_APPROVAL,
            rule_id=rule_id,
            reason=reason,
            action_digest=digest,
            approval_id=None,
        )

    def _record_approval_mismatch(
        self, task_id: str, approval_id: str, reason: str
    ) -> None:
        payload: dict[str, JsonValue] = {
            "approval_id": approval_id,
            "reason": reason,
            "feedback": {
                "kind": "POLICY_VIOLATION",
                "message": "persisted approval no longer authorizes the action",
                "can_continue": True,
            },
        }
        try:
            task = self.database.tasks.get(task_id)
        except KeyError:
            task = self.database.tasks.get(self.task_id)
            task_id = task.id
        if task.status is TaskStatus.WAITING_APPROVAL:
            self.database.tasks.transition_status(
                task_id,
                TaskStatus.RUNNING,
                event_type="approval_mismatch",
                payload=payload,
            )
        else:
            self.audit.append(task_id, "approval_mismatch", payload)


def action_digest(
    *,
    task_id: str,
    action: Action,
    workspace: Path,
    policy_version: str = "1.0",
) -> str:
    """Hash the exact canonical JSON bytes mandated by the approval contract."""
    canonical_workspace = workspace.resolve(strict=True)
    canonical_object = {
        "task_id": task_id,
        "tool": action.tool.value,
        "arguments": _normalized_arguments(action),
        "workspace": str(canonical_workspace),
        "policy_version": policy_version,
    }
    canonical_json = json.dumps(
        canonical_object,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _normalized_arguments(action: Action) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key, value in action.arguments.items():
        if key in _PATH_ARGUMENT_KEYS:
            if not isinstance(value, str):
                raise TypeError(f"{key} must be a path string")
            normalized[key] = normalize_relative_posix(value)
        else:
            normalized[key] = value
    return normalized


def _file_path_values(action: Action) -> list[str]:
    arguments = action.arguments
    if action.tool is ToolName.MOVE_FILE:
        source = arguments.get("source", arguments.get("source_path"))
        destination = arguments.get("destination", arguments.get("destination_path"))
        if not isinstance(source, str) or not isinstance(destination, str):
            raise TypeError("move_file requires source and destination paths")
        return [source, destination]

    path = arguments.get("path")
    if not isinstance(path, str):
        raise TypeError(f"{action.tool.value} requires a path")
    return [path]


def _require_non_empty_text(arguments: dict[str, JsonValue], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be non-empty text")
    return value


def _validated_argv(arguments: dict[str, JsonValue]) -> list[str]:
    if set(arguments) != {"argv"}:
        raise TypeError("command arguments must contain only argv")
    argv = arguments["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
    ):
        raise TypeError("argv must be a non-empty array of non-empty strings")
    return cast(list[str], argv)


def _is_hard_denied(argv: list[str]) -> bool:
    executable = Path(argv[0]).name.lower()
    if (
        executable in _HARD_DENIED_EXECUTABLES
        or executable.endswith(("sh", "powershell"))
        or (
            executable == "busybox"
            and len(argv) > 1
            and argv[1].lower() in _BUSYBOX_SHELL_APPLETS
        )
    ):
        return True
    if argv[0] == "rg" and any(_is_rg_hard_option(argument) for argument in argv[1:]):
        return True
    if executable == "git" and any(
        argument == "--output" or argument.startswith("--output=") for argument in argv[1:]
    ):
        return True
    git_invocation = _git_subcommand(argv)
    if git_invocation is not None:
        subcommand, arguments = git_invocation
        if subcommand == "reset" and "--hard" in arguments:
            return True
        if subcommand == "clean":
            return True
        if subcommand == "push" and any(_is_force_push_option(argument) for argument in arguments):
            return True
        if subcommand == "checkout" and "--" in arguments:
            return True
    return executable == "rm" and any(argument == "/" for argument in argv[1:])


def _is_force_push_option(argument: str) -> bool:
    return argument == "--force" or (
        argument.startswith("-") and not argument.startswith("--") and "f" in argument[1:]
    )


def _git_subcommand(argv: list[str]) -> tuple[str, list[str]] | None:
    if Path(argv[0]).name != "git":
        return None

    index = 1
    options_with_values = {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
        "--exec-path",
    }
    while index < len(argv):
        argument = argv[index]
        if argument in options_with_values:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument, argv[index + 1 :]
    return None


def _contains_shell_syntax(argv: list[str]) -> bool:
    return any(
        argument in _SHELL_TOKENS or "$(" in argument or "`" in argument
        for argument in argv
    )


def _approved_read_command_paths(argv: list[str]) -> list[str] | None:
    if argv[0] == "rg":
        return _rg_read_paths(argv)
    if argv[0] != "git" or len(argv) < 2:
        return None

    if argv[1] == "status":
        return _git_read_paths(argv[2:], _is_safe_git_status_flag)
    if argv[1] == "diff":
        return _git_read_paths(argv[2:], _is_safe_git_diff_flag)
    return None


def _is_rg_hard_option(argument: str) -> bool:
    return (
        argument in _RG_HARD_OPTIONS
        or (argument.startswith("-f") and len(argument) > 2)
        or any(
            argument.startswith(f"{option}=") for option in ("--pre", "--pre-glob", "--file")
        )
    )


def _rg_read_paths(argv: list[str]) -> list[str] | None:
    arguments = argv[1:]
    if not arguments:
        return None
    if arguments[0] == "--files":
        file_paths = arguments[1:]
        return file_paths if all(not path.startswith("-") for path in file_paths) else None

    index = 0
    while index < len(arguments) and arguments[index] in _RG_SAFE_FLAGS:
        index += 1
    if index >= len(arguments) or arguments[index].startswith("-"):
        return None
    search_paths: list[str] = []
    for argument in arguments[index + 1 :]:
        if argument in _RG_SAFE_FLAGS:
            continue
        if argument.startswith("-"):
            return None
        search_paths.append(argument)
    return search_paths


def _git_read_paths(
    arguments: list[str], is_safe_flag: Callable[[str], bool]
) -> list[str] | None:
    if "--" in arguments:
        separator = arguments.index("--")
        flags = arguments[:separator]
        paths = arguments[separator + 1 :]
    else:
        flags = arguments
        paths = []
    if not all(is_safe_flag(flag) for flag in flags):
        return None
    return paths


def _is_safe_git_status_flag(flag: str) -> bool:
    if flag in _GIT_STATUS_SAFE_FLAGS:
        return True
    return flag in {"-u", "-uno", "-unormal", "-uall"} or flag in {
        "--untracked-files=no",
        "--untracked-files=normal",
        "--untracked-files=all",
    }


def _is_safe_git_diff_flag(flag: str) -> bool:
    if flag in _GIT_DIFF_SAFE_FLAGS:
        return True
    return (
        (flag.startswith("-U") and flag[2:].isdigit())
        or (flag.startswith("--unified=") and flag.removeprefix("--unified=").isdigit())
        or flag in {"--color=always", "--color=never", "--color=auto"}
    )


def _command_path_candidates(argv: list[str]) -> list[str]:
    candidates: list[str] = []
    for argument in argv[1:]:
        if argument.startswith("-"):
            if "=" in argument:
                value = argument.split("=", maxsplit=1)[1]
                if value:
                    candidates.append(value)
            continue
        candidates.append(argument)
    return candidates


def _decision(outcome: GovernanceOutcome, rule_id: str, reason: str) -> GovernanceDecision:
    return GovernanceDecision(
        outcome=outcome,
        rule_id=rule_id,
        reason=reason,
        action_digest=None,
        approval_id=None,
    )
