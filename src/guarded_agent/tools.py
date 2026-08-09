"""Strict tool dispatch with execution-time workspace confinement."""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, ValidationError

from guarded_agent.domain import Action, Settings, StrictDTO, ToolName, ToolResult
from guarded_agent.memory import MemorySource, MemoryStore, MemoryTrust
from guarded_agent.paths import PolicyDenied, is_sensitive_path, normalize_relative_posix
from guarded_agent.redaction import Redactor
from guarded_agent.subprocesses import CommandResult, CommandRunner

PathArgument = Annotated[StrictStr, Field(min_length=1, max_length=4096)]
TextArgument = Annotated[StrictStr, Field(min_length=1, max_length=65_536)]
CommandArgument = Annotated[StrictStr, Field(min_length=1, max_length=4096)]
CommandArgv = Annotated[list[CommandArgument], Field(min_length=1, max_length=128)]


class ListDirectoryArguments(StrictDTO):
    path: PathArgument


class ReadFileArguments(StrictDTO):
    path: PathArgument


class SearchTextArguments(StrictDTO):
    path: PathArgument
    query: TextArgument
    max_results: StrictInt = Field(default=100, ge=1, le=1000)


class WriteFileArguments(StrictDTO):
    path: PathArgument
    content: StrictStr


class DeleteFileArguments(StrictDTO):
    path: PathArgument


class MoveFileArguments(StrictDTO):
    source: PathArgument
    destination: PathArgument


class GitStatusArguments(StrictDTO):
    pass


class GitDiffArguments(StrictDTO):
    pass


class RunCommandArguments(StrictDTO):
    argv: CommandArgv


class RunValidatorArguments(StrictDTO):
    argv: CommandArgv


class SaveMemoryArguments(StrictDTO):
    category: TextArgument
    content: TextArgument


class RetrieveMemoryArguments(StrictDTO):
    query: TextArgument
    limit: StrictInt = Field(default=10, ge=1, le=100)


class CompleteArguments(StrictDTO):
    summary: Annotated[StrictStr, Field(max_length=65_536)] = ""


class CannotContinueArguments(StrictDTO):
    reason: TextArgument


class ListDirectoryInvocation(StrictDTO):
    tool: Literal[ToolName.LIST_DIRECTORY]
    arguments: ListDirectoryArguments


class ReadFileInvocation(StrictDTO):
    tool: Literal[ToolName.READ_FILE]
    arguments: ReadFileArguments


class SearchTextInvocation(StrictDTO):
    tool: Literal[ToolName.SEARCH_TEXT]
    arguments: SearchTextArguments


class WriteFileInvocation(StrictDTO):
    tool: Literal[ToolName.WRITE_FILE]
    arguments: WriteFileArguments


class DeleteFileInvocation(StrictDTO):
    tool: Literal[ToolName.DELETE_FILE]
    arguments: DeleteFileArguments


class MoveFileInvocation(StrictDTO):
    tool: Literal[ToolName.MOVE_FILE]
    arguments: MoveFileArguments


class GitStatusInvocation(StrictDTO):
    tool: Literal[ToolName.GIT_STATUS]
    arguments: GitStatusArguments


class GitDiffInvocation(StrictDTO):
    tool: Literal[ToolName.GIT_DIFF]
    arguments: GitDiffArguments


class RunCommandInvocation(StrictDTO):
    tool: Literal[ToolName.RUN_COMMAND]
    arguments: RunCommandArguments


class RunValidatorInvocation(StrictDTO):
    tool: Literal[ToolName.RUN_VALIDATOR]
    arguments: RunValidatorArguments


class SaveMemoryInvocation(StrictDTO):
    tool: Literal[ToolName.SAVE_MEMORY]
    arguments: SaveMemoryArguments


class RetrieveMemoryInvocation(StrictDTO):
    tool: Literal[ToolName.RETRIEVE_MEMORY]
    arguments: RetrieveMemoryArguments


class CompleteInvocation(StrictDTO):
    tool: Literal[ToolName.COMPLETE]
    arguments: CompleteArguments


class CannotContinueInvocation(StrictDTO):
    tool: Literal[ToolName.CANNOT_CONTINUE]
    arguments: CannotContinueArguments


type ToolInvocation = Annotated[
    ListDirectoryInvocation
    | ReadFileInvocation
    | SearchTextInvocation
    | WriteFileInvocation
    | DeleteFileInvocation
    | MoveFileInvocation
    | GitStatusInvocation
    | GitDiffInvocation
    | RunCommandInvocation
    | RunValidatorInvocation
    | SaveMemoryInvocation
    | RetrieveMemoryInvocation
    | CompleteInvocation
    | CannotContinueInvocation,
    Field(discriminator="tool"),
]

_INVOCATION_ADAPTER: TypeAdapter[ToolInvocation] = TypeAdapter(ToolInvocation)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_MAX_DIRECTORY_ENTRIES = 10_000
_MAX_SEARCH_FILES = 10_000


class ToolRegistry:
    """Validate the per-tool union, then dispatch inside one opened workspace."""

    def __init__(
        self,
        workspace: Path,
        settings: Settings,
        redactor: Redactor,
        *,
        runner: CommandRunner | None = None,
        memory_store: MemoryStore | None = None,
        workspace_id: str | None = None,
    ) -> None:
        canonical_workspace = workspace.resolve(strict=True)
        if not canonical_workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self._workspace = canonical_workspace
        self._root_fd = os.open(canonical_workspace, _DIRECTORY_FLAGS)
        self._settings = settings.model_copy(deep=True)
        self._validation_commands = frozenset(
            tuple(command) for command in settings.validation_commands
        )
        self._redactor = redactor
        self._runner = runner or CommandRunner(
            redactor=redactor,
            max_output_bytes=settings.max_output_bytes,
        )
        self._memory_store = memory_store
        self._workspace_id = workspace_id

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def execute(self, action: Action) -> ToolResult:
        """Reject invalid arguments before dispatch and structure every execution error."""
        started = monotonic()
        try:
            invocation = _INVOCATION_ADAPTER.validate_python(
                {"tool": action.tool, "arguments": action.arguments}
            )
        except ValidationError as error:
            return self._failure(
                action.tool,
                f"invalid action: {error}",
                started,
            )

        try:
            return self._dispatch(invocation, started)
        except (OSError, PolicyDenied, ValueError) as error:
            return self._failure(action.tool, f"tool failed: {error}", started)

    def _dispatch(self, invocation: ToolInvocation, started: float) -> ToolResult:
        if isinstance(invocation, ListDirectoryInvocation):
            output, truncated = self._list_directory(invocation.arguments.path)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, ReadFileInvocation):
            output, truncated = self._read_file(invocation.arguments.path)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, SearchTextInvocation):
            output, truncated = self._search_text(invocation.arguments)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, WriteFileInvocation):
            self._write_file(invocation.arguments.path, invocation.arguments.content)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.path],
            )
        if isinstance(invocation, DeleteFileInvocation):
            self._delete_file(invocation.arguments.path)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.path],
            )
        if isinstance(invocation, MoveFileInvocation):
            self._move_file(invocation.arguments.source, invocation.arguments.destination)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.source, invocation.arguments.destination],
            )
        if isinstance(invocation, GitStatusInvocation):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    ["git", "status", "--short"],
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, GitDiffInvocation):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color"],
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, RunCommandInvocation):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    invocation.arguments.argv,
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, RunValidatorInvocation):
            if tuple(invocation.arguments.argv) not in self._validation_commands:
                return self._failure(
                    invocation.tool,
                    "validator argv is not configured",
                    started,
                )
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    invocation.arguments.argv,
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, SaveMemoryInvocation):
            return self._save_memory(invocation, started)
        if isinstance(invocation, RetrieveMemoryInvocation):
            return self._retrieve_memory(invocation, started)
        if isinstance(invocation, CompleteInvocation):
            return self._success(invocation.tool, invocation.arguments.summary, started)
        return self._success(invocation.tool, invocation.arguments.reason, started)

    def _list_directory(self, path: str) -> tuple[str, bool]:
        parts = _safe_parts(path)
        directory_fd = self._open_directory(parts)
        try:
            entries = os.listdir(directory_fd)
            if len(entries) > _MAX_DIRECTORY_ENTRIES:
                raise ValueError("directory entry limit exceeded")
            output = _OutputBuffer(self._settings.max_output_bytes)
            for index, entry in enumerate(sorted(entries)):
                prefix = "" if index == 0 else "\n"
                output.add(f"{prefix}{entry}".encode())
            return self._redactor.redact(output.text()), output.truncated
        finally:
            os.close(directory_fd)

    def _read_file(self, path: str) -> tuple[str, bool]:
        parts = _safe_parts(path)
        with self._parent_fd(parts) as (parent_fd, name):
            file_fd = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("read_file target must be a regular file")
            output = _OutputBuffer(self._settings.max_output_bytes)
            if file_stat.st_size <= self._settings.max_output_bytes:
                output.add(os.pread(file_fd, self._settings.max_output_bytes + 1, 0))
            else:
                head_size = self._settings.max_output_bytes // 2
                tail_size = self._settings.max_output_bytes - head_size
                head = os.pread(file_fd, head_size, 0)
                tail = os.pread(file_fd, tail_size, max(0, file_stat.st_size - tail_size))
                output.add(head)
                output.note_omitted(max(0, file_stat.st_size - len(head) - len(tail)))
                output.add(tail)
            return self._redactor.redact(output.text()), output.truncated
        finally:
            os.close(file_fd)

    def _search_text(self, arguments: SearchTextArguments) -> tuple[str, bool]:
        parts = _safe_parts(arguments.path)
        target_fd = self._open_target(parts)
        output = _OutputBuffer(self._settings.max_output_bytes)
        matches = 0
        files_seen = 0
        try:
            target_stat = os.fstat(target_fd)
            if stat.S_ISREG(target_stat.st_mode):
                matches = self._search_file(
                    target_fd,
                    arguments.path,
                    arguments.query,
                    arguments.max_results,
                    output,
                )
            elif stat.S_ISDIR(target_stat.st_mode):
                stack: list[tuple[str, int]] = [(arguments.path, target_fd)]
                target_fd = -1
                try:
                    while stack and matches < arguments.max_results:
                        directory_path, directory_fd = stack.pop()
                        try:
                            child_directories: list[tuple[str, str]] = []
                            for name in sorted(os.listdir(directory_fd)):
                                entry_stat = os.stat(
                                    name, dir_fd=directory_fd, follow_symlinks=False
                                )
                                display_path = f"{directory_path}/{name}"
                                if stat.S_ISDIR(entry_stat.st_mode):
                                    child_directories.append((name, display_path))
                                elif stat.S_ISREG(entry_stat.st_mode):
                                    files_seen += 1
                                    if files_seen > _MAX_SEARCH_FILES:
                                        raise ValueError("search file limit exceeded")
                                    file_fd = os.open(name, _FILE_READ_FLAGS, dir_fd=directory_fd)
                                    try:
                                        matches += self._search_file(
                                            file_fd,
                                            display_path,
                                            arguments.query,
                                            arguments.max_results - matches,
                                            output,
                                        )
                                    finally:
                                        os.close(file_fd)
                                if matches >= arguments.max_results:
                                    break
                            for name, display_path in reversed(child_directories):
                                try:
                                    child_fd = os.open(
                                        name, _DIRECTORY_FLAGS, dir_fd=directory_fd
                                    )
                                except OSError:
                                    continue
                                stack.append((display_path, child_fd))
                        finally:
                            os.close(directory_fd)
                finally:
                    for _, directory_fd in stack:
                        os.close(directory_fd)
            else:
                raise ValueError("search_text target must be a file or directory")
        finally:
            if target_fd >= 0:
                os.close(target_fd)
        return self._redactor.redact(output.text()), output.truncated

    def _search_file(
        self,
        file_fd: int,
        display_path: str,
        query: str,
        remaining: int,
        output: _OutputBuffer,
    ) -> int:
        raw = bytearray()
        while len(raw) <= self._settings.max_output_bytes:
            chunk = os.read(file_fd, min(8192, self._settings.max_output_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        found = 0
        for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if query in line:
                prefix = "" if output.empty else "\n"
                output.add(f"{prefix}{display_path}:{line_number}:{line}".encode())
                found += 1
                if found >= remaining:
                    break
        return found

    def _write_file(self, path: str, content: str) -> None:
        encoded = content.encode("utf-8")
        if len(encoded) > min(self._settings.max_output_bytes, 65_536):
            raise ValueError("write content exceeds the configured byte limit")
        parts = _safe_parts(path)
        with self._parent_fd(parts) as (parent_fd, name):
            temporary = f".guarded-agent-write-{secrets.token_hex(16)}"
            temporary_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(temporary_fd, view)
                    view = view[written:]
                os.fsync(temporary_fd)
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                raise
            finally:
                os.close(temporary_fd)

    def _delete_file(self, path: str) -> None:
        parts = _safe_parts(path)
        with self._parent_fd(parts) as (parent_fd, name):
            target_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(target_stat.st_mode):
                raise ValueError("delete_file cannot delete a directory")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)

    def _move_file(self, source: str, destination: str) -> None:
        source_parts = _safe_parts(source)
        destination_parts = _safe_parts(destination)
        with (
            self._parent_fd(source_parts) as (source_fd, source_name),
            self._parent_fd(destination_parts) as (destination_fd, destination_name),
        ):
            source_stat = os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
            if stat.S_ISDIR(source_stat.st_mode):
                raise ValueError("move_file cannot move a directory")
            os.replace(
                source_name,
                destination_name,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )
            os.fsync(source_fd)
            if destination_fd != source_fd:
                os.fsync(destination_fd)

    def _save_memory(self, invocation: SaveMemoryInvocation, started: float) -> ToolResult:
        if self._memory_store is None or self._workspace_id is None:
            return self._failure(invocation.tool, "memory store is not configured", started)
        entry = self._memory_store.add(
            self._workspace_id,
            invocation.arguments.category,
            invocation.arguments.content,
            MemorySource.USER,
            MemoryTrust.CONFIRMED,
        )
        return self._success(invocation.tool, entry.id, started, changes=["memory"])

    def _retrieve_memory(
        self, invocation: RetrieveMemoryInvocation, started: float
    ) -> ToolResult:
        if self._memory_store is None or self._workspace_id is None:
            return self._failure(invocation.tool, "memory store is not configured", started)
        entries = self._memory_store.search(
            self._workspace_id,
            invocation.arguments.query,
            invocation.arguments.limit,
        )
        output = json.dumps(
            [
                {"id": entry.id, "category": entry.category, "content": entry.content}
                for entry in entries
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self._success(invocation.tool, self._redactor.redact(output), started)

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        current_fd = os.dup(self._root_fd)
        try:
            for part in parts:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except BaseException:
            os.close(current_fd)
            raise

    def _open_target(self, parts: tuple[str, ...]) -> int:
        with self._parent_fd(parts) as (parent_fd, name):
            return os.open(name, _FILE_READ_FLAGS, dir_fd=parent_fd)

    @contextmanager
    def _parent_fd(self, parts: tuple[str, ...]) -> Iterator[tuple[int, str]]:
        parent_fd = self._open_directory(parts[:-1])
        try:
            yield parent_fd, parts[-1]
        finally:
            os.close(parent_fd)

    def _stable_cwd(self) -> Path:
        if self._root_fd < 0:
            raise ValueError("tool registry is closed")
        return Path(f"/proc/self/fd/{self._root_fd}")

    def _success(
        self,
        tool: ToolName,
        stdout: str,
        started: float,
        *,
        stdout_truncated: bool = False,
        changes: list[str] | None = None,
    ) -> ToolResult:
        if not stdout_truncated:
            output = _OutputBuffer(self._settings.max_output_bytes)
            output.add(self._redactor.redact(stdout).encode())
            stdout = output.text()
            stdout_truncated = output.truncated
        return ToolResult(
            tool=tool,
            exit_code=0,
            stdout=stdout,
            stderr="",
            stdout_truncated=stdout_truncated,
            stderr_truncated=False,
            duration_ms=_duration_ms(started),
            changes=changes or [],
        )

    def _failure(self, tool: ToolName, message: str, started: float) -> ToolResult:
        output = _OutputBuffer(self._settings.max_output_bytes)
        output.add(self._redactor.redact(message).encode())
        return ToolResult(
            tool=tool,
            exit_code=None,
            stdout="",
            stderr=output.text(),
            stdout_truncated=False,
            stderr_truncated=output.truncated,
            duration_ms=_duration_ms(started),
            changes=[],
        )

    @staticmethod
    def _command_result(tool: ToolName, result: CommandResult) -> ToolResult:
        return ToolResult(
            tool=tool,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            duration_ms=result.duration_ms,
            changes=[],
        )


class _OutputBuffer:
    def __init__(self, limit: int) -> None:
        self._limit = min(limit, 65_536)
        self._head_limit = self._limit // 2
        self._tail_limit = self._limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self._total = 0

    @property
    def empty(self) -> bool:
        return self._total == 0

    @property
    def truncated(self) -> bool:
        return self._total > self._limit

    def add(self, value: bytes) -> None:
        self._total += len(value)
        remaining = self._head_limit - len(self._head)
        if remaining:
            self._head.extend(value[:remaining])
            value = value[remaining:]
        self._tail.extend(value)
        if len(self._tail) > self._tail_limit:
            del self._tail[: len(self._tail) - self._tail_limit]

    def note_omitted(self, byte_count: int) -> None:
        self._total += byte_count

    def text(self) -> str:
        if self.truncated:
            raw = bytes(self._head) + b"\n... output truncated ...\n" + bytes(self._tail)
        else:
            raw = bytes(self._head + self._tail)
        return raw.decode("utf-8", errors="replace")


def _safe_parts(path: str) -> tuple[str, ...]:
    normalized = normalize_relative_posix(path)
    if is_sensitive_path(normalized):
        raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
    return PurePosixPath(normalized).parts


def _duration_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
