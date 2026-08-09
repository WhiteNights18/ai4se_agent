"""Strict tool dispatch with execution-time workspace confinement."""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Self

from guarded_agent.domain import (
    Action,
    CompleteAction,
    DeleteFileAction,
    GitDiffAction,
    GitStatusAction,
    ListDirectoryAction,
    MoveFileAction,
    ReadFileAction,
    RetrieveMemoryAction,
    RunCommandAction,
    RunValidatorAction,
    SaveMemoryAction,
    SearchTextAction,
    SearchTextArguments,
    Settings,
    ToolAction,
    ToolName,
    ToolResult,
    WriteFileAction,
)
from guarded_agent.memory import MemorySource, MemoryStore, MemoryTrust
from guarded_agent.paths import PolicyDenied, is_sensitive_path, normalize_relative_posix
from guarded_agent.redaction import Redactor
from guarded_agent.subprocesses import CommandResult, CommandRunner

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_MAX_DIRECTORY_ENTRIES = 10_000
_MAX_SEARCH_DEPTH = 64


class MutationStateUncertain(RuntimeError):
    """A mutating syscall completed but its final filesystem state cannot be trusted."""

    def __init__(self, message: str, *, changes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.changes = changes


@dataclass(frozen=True)
class _MutationTarget:
    """A canonical mutation target plus identities captured during resolution."""

    parts: tuple[str, ...]
    expected: os.stat_result | None
    expected_parent: os.stat_result


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
        git_executable: Path | None = None,
    ) -> None:
        canonical_workspace = workspace.resolve(strict=True)
        if not canonical_workspace.is_dir():
            raise ValueError("workspace must be a directory")
        resolved_git = _resolve_git_executable(git_executable)
        self._workspace = canonical_workspace
        self._root_fd = _open_workspace_root(canonical_workspace)
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
        self._git_executable = resolved_git

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def execute(self, action: ToolAction | Action) -> ToolResult:
        """Execute one action that passed the public strict domain parse boundary."""
        started = monotonic()
        invocation = action.root if isinstance(action, Action) else action

        try:
            return self._dispatch(invocation, started)
        except MutationStateUncertain as error:
            return self._failure(
                action.tool,
                f"state_uncertain: {error}",
                started,
                changes=list(error.changes),
            )
        except (OSError, PolicyDenied, ValueError) as error:
            return self._failure(action.tool, f"tool failed: {error}", started)

    def _dispatch(self, invocation: ToolAction, started: float) -> ToolResult:
        if isinstance(invocation, ListDirectoryAction):
            output, truncated = self._list_directory(invocation.arguments.path)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, ReadFileAction):
            output, truncated = self._read_file(invocation.arguments.path)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, SearchTextAction):
            output, truncated = self._search_text(invocation.arguments)
            return self._success(
                invocation.tool,
                output,
                started,
                stdout_truncated=truncated,
            )
        if isinstance(invocation, WriteFileAction):
            self._write_file(invocation.arguments.path, invocation.arguments.content)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.path],
            )
        if isinstance(invocation, DeleteFileAction):
            self._delete_file(invocation.arguments.path)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.path],
            )
        if isinstance(invocation, MoveFileAction):
            self._move_file(invocation.arguments.source, invocation.arguments.destination)
            return self._success(
                invocation.tool,
                "",
                started,
                changes=[invocation.arguments.source, invocation.arguments.destination],
            )
        if isinstance(invocation, GitStatusAction):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    [*self._git_prefix(), "status", "--short"],
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, GitDiffAction):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    [
                        *self._git_prefix(),
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-color",
                    ],
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, RunCommandAction):
            return self._command_result(
                invocation.tool,
                self._runner.run(
                    invocation.arguments.argv,
                    self._stable_cwd(),
                    self._settings.command_timeout_seconds,
                ),
            )
        if isinstance(invocation, RunValidatorAction):
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
        if isinstance(invocation, SaveMemoryAction):
            return self._save_memory(invocation, started)
        if isinstance(invocation, RetrieveMemoryAction):
            return self._retrieve_memory(invocation, started)
        if isinstance(invocation, CompleteAction):
            return self._success(invocation.tool, invocation.arguments.summary, started)
        return self._success(invocation.tool, invocation.arguments.reason, started)

    def _list_directory(self, path: str) -> tuple[str, bool]:
        directory_fd = self._open_resolved_target(path, _DIRECTORY_FLAGS)
        try:
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise ValueError("list_directory target must be a directory")
            entries: list[str] = []
            with os.scandir(directory_fd) as iterator:
                for scanned_entry in iterator:
                    entries.append(scanned_entry.name)
                    if len(entries) > _MAX_DIRECTORY_ENTRIES:
                        raise ValueError("directory entry limit exceeded")
            output = _OutputBuffer(self._settings.max_output_bytes)
            for index, entry_name in enumerate(sorted(entries)):
                prefix = "" if index == 0 else "\n"
                output.add(f"{prefix}{entry_name}".encode())
            return self._finish_output(output)
        finally:
            os.close(directory_fd)

    def _read_file(self, path: str) -> tuple[str, bool]:
        file_fd = self._open_resolved_target(path, _FILE_READ_FLAGS)
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
            return self._finish_output(output)
        finally:
            os.close(file_fd)

    def _search_text(self, arguments: SearchTextArguments) -> tuple[str, bool]:
        target_parts, expected_target = self._resolved_target(arguments.path)
        target_fd = self._open_expected_target(
            target_parts,
            _FILE_READ_FLAGS,
            expected_target,
        )
        output = _OutputBuffer(self._settings.max_output_bytes)
        matches = 0
        entries_seen = 0
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
                directories = deque(
                    [(target_parts, arguments.path, 0, _file_identity(target_stat))]
                )
                os.close(target_fd)
                target_fd = -1
                while directories and matches < arguments.max_results:
                    directory_parts, directory_path, depth, expected_identity = (
                        directories.popleft()
                    )
                    directory_fd = self._open_target(directory_parts, _DIRECTORY_FLAGS)
                    try:
                        if _file_identity(os.fstat(directory_fd)) != expected_identity:
                            raise ValueError("search directory identity changed")
                        names: list[str] = []
                        with os.scandir(directory_fd) as iterator:
                            for entry in iterator:
                                entries_seen += 1
                                if entries_seen > _MAX_DIRECTORY_ENTRIES:
                                    raise ValueError("search tree entry limit exceeded")
                                names.append(entry.name)
                        for name in sorted(names):
                            entry_stat = os.stat(
                                name, dir_fd=directory_fd, follow_symlinks=False
                            )
                            display_path = f"{directory_path}/{name}"
                            if stat.S_ISDIR(entry_stat.st_mode):
                                child_depth = depth + 1
                                if child_depth > _MAX_SEARCH_DEPTH:
                                    raise ValueError("search directory depth limit exceeded")
                                directories.append(
                                    (
                                        (*directory_parts, name),
                                        display_path,
                                        child_depth,
                                        _file_identity(entry_stat),
                                    )
                                )
                            elif stat.S_ISREG(entry_stat.st_mode):
                                file_fd = os.open(
                                    name,
                                    _FILE_READ_FLAGS,
                                    dir_fd=directory_fd,
                                )
                                try:
                                    if _file_identity(os.fstat(file_fd)) != _file_identity(
                                        entry_stat
                                    ):
                                        raise ValueError("search file identity changed")
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
                    finally:
                        os.close(directory_fd)
            else:
                raise ValueError("search_text target must be a file or directory")
        finally:
            if target_fd >= 0:
                os.close(target_fd)
        return self._finish_output(output)

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
        target = self._resolved_mutation_target(path, must_exist=False)
        with self._mutation_parent_fd(target) as (parent_fd, name):
            mode = 0o600
            try:
                existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if target.expected is not None:
                    raise ValueError("write_file target identity changed before replacement")
            else:
                if target.expected is None or _file_identity(existing) != _file_identity(
                    target.expected
                ):
                    raise ValueError("write_file target identity changed before replacement")
                if stat.S_ISREG(existing.st_mode):
                    mode = stat.S_IMODE(existing.st_mode)
            temporary = f".guarded-agent-write-{secrets.token_hex(16)}"
            temporary_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.fchmod(temporary_fd, mode)
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
        target = self._resolved_mutation_target(path, must_exist=True)
        with self._mutation_parent_fd(target) as (parent_fd, name):
            target_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if target.expected is None or _file_identity(target_stat) != _file_identity(
                target.expected
            ):
                raise ValueError("delete_file target identity changed before deletion")
            if stat.S_ISDIR(target_stat.st_mode):
                raise ValueError("delete_file cannot delete a directory")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)

    def _move_file(self, source: str, destination: str) -> None:
        normalized_source = normalize_relative_posix(source)
        normalized_destination = normalize_relative_posix(destination)
        source_target = self._resolved_mutation_target(source, must_exist=True)
        destination_target = self._resolved_mutation_target(destination, must_exist=False)
        rename_completed = False
        try:
            with (
                self._mutation_parent_fd(source_target) as (source_fd, source_name),
                self._mutation_parent_fd(destination_target) as (
                    destination_fd,
                    destination_name,
                ),
            ):
                try:
                    _move_verified(
                        source_fd,
                        source_name,
                        destination_fd,
                        destination_name,
                        expected_source=source_target.expected,
                        expected_destination=destination_target.expected,
                    )
                except MutationStateUncertain as error:
                    rename_completed = True
                    raise MutationStateUncertain(
                        str(error),
                        changes=(normalized_source, normalized_destination),
                    ) from error
                rename_completed = True
                os.fsync(source_fd)
                if destination_fd != source_fd:
                    os.fsync(destination_fd)
        except MutationStateUncertain:
            raise
        except Exception as error:
            if rename_completed:
                raise MutationStateUncertain(
                    "move_file final state could not be verified after rename",
                    changes=(normalized_source, normalized_destination),
                ) from error
            raise

    def _save_memory(self, invocation: SaveMemoryAction, started: float) -> ToolResult:
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
        self, invocation: RetrieveMemoryAction, started: float
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

    def _open_target(self, parts: tuple[str, ...], flags: int) -> int:
        with self._parent_fd(parts) as (parent_fd, name):
            return os.open(name, flags, dir_fd=parent_fd)

    def _open_resolved_target(self, path: str, flags: int) -> int:
        parts, expected = self._resolved_target(path)
        return self._open_expected_target(parts, flags, expected)

    def _open_expected_target(
        self,
        parts: tuple[str, ...],
        flags: int,
        expected: os.stat_result,
    ) -> int:
        descriptor = self._open_target(parts, flags)
        actual = os.fstat(descriptor)
        if (actual.st_dev, actual.st_ino, stat.S_IFMT(actual.st_mode)) != (
            expected.st_dev,
            expected.st_ino,
            stat.S_IFMT(expected.st_mode),
        ):
            os.close(descriptor)
            raise ValueError("target identity changed during secure open")
        return descriptor

    def _resolved_target(self, path: str) -> tuple[tuple[str, ...], os.stat_result]:
        normalized = normalize_relative_posix(path)
        if is_sensitive_path(normalized):
            raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
        stable_root = self._stable_cwd().resolve(strict=True)
        try:
            resolved = stable_root.joinpath(*PurePosixPath(normalized).parts).resolve(strict=True)
            relative = resolved.relative_to(stable_root)
        except ValueError as error:
            raise PolicyDenied("path resolves outside the workspace") from error
        if relative == Path("."):
            raise PolicyDenied("path must resolve strictly inside the workspace")
        relative_posix = relative.as_posix()
        if is_sensitive_path(relative_posix):
            raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
        expected = os.stat(resolved, follow_symlinks=False)
        return PurePosixPath(relative_posix).parts, expected

    def _resolved_mutation_target(
        self,
        path: str,
        *,
        must_exist: bool,
    ) -> _MutationTarget:
        """Resolve an existing target or its nearest existing parent through the root fd."""
        normalized = normalize_relative_posix(path)
        if is_sensitive_path(normalized):
            raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
        submitted_parts = PurePosixPath(normalized).parts
        stable_root = self._stable_cwd()
        canonical_root = stable_root.resolve(strict=True)

        resolved_prefix: Path | None = None
        prefix_length = len(submitted_parts)
        while prefix_length >= 0:
            candidate = stable_root.joinpath(*submitted_parts[:prefix_length])
            try:
                resolved_prefix = candidate.resolve(strict=True)
                break
            except FileNotFoundError:
                prefix_length -= 1
            except RuntimeError as error:
                raise ValueError("path contains an unresolvable symlink") from error
        if resolved_prefix is None:
            raise ValueError("workspace root is unavailable")
        try:
            resolved_prefix.relative_to(canonical_root)
        except ValueError as error:
            raise PolicyDenied("path resolves outside the workspace") from error

        remaining = submitted_parts[prefix_length:]
        canonical = resolved_prefix.joinpath(*remaining)
        relative = canonical.relative_to(canonical_root)
        if relative == Path("."):
            raise PolicyDenied("path must resolve strictly inside the workspace")
        relative_posix = relative.as_posix()
        if is_sensitive_path(relative_posix):
            raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
        parts = PurePosixPath(relative_posix).parts

        if remaining:
            expected = None
            if must_exist:
                raise ValueError("mutation target does not exist")
        else:
            expected = os.stat(resolved_prefix, follow_symlinks=False)
        parent = canonical.parent.resolve(strict=True)
        if parent.relative_to(canonical_root) == Path("."):
            parent_parts: tuple[str, ...] = ()
        else:
            parent_parts = PurePosixPath(parent.relative_to(canonical_root).as_posix()).parts
        if parent_parts != parts[:-1]:
            raise ValueError("mutation target parent does not exist")
        expected_parent = os.stat(parent, follow_symlinks=False)
        return _MutationTarget(parts, expected, expected_parent)

    @contextmanager
    def _mutation_parent_fd(
        self,
        target: _MutationTarget,
    ) -> Iterator[tuple[int, str]]:
        with self._parent_fd(target.parts) as (parent_fd, name):
            if _file_identity(os.fstat(parent_fd)) != _file_identity(target.expected_parent):
                raise ValueError("mutation target parent identity changed during secure open")
            yield parent_fd, name

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

    def _git_prefix(self) -> list[str]:
        return [
            str(self._git_executable),
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
        ]

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
            output.add(stdout.encode())
            stdout, stdout_truncated = self._finish_output(output)
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

    def _failure(
        self,
        tool: ToolName,
        message: str,
        started: float,
        *,
        changes: list[str] | None = None,
    ) -> ToolResult:
        output = _OutputBuffer(self._settings.max_output_bytes)
        output.add(message.encode())
        stderr, stderr_truncated = self._finish_output(output)
        return ToolResult(
            tool=tool,
            exit_code=None,
            stdout="",
            stderr=stderr,
            stdout_truncated=False,
            stderr_truncated=stderr_truncated,
            duration_ms=_duration_ms(started),
            changes=changes or [],
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

    def _finish_output(self, output: _OutputBuffer) -> tuple[str, bool]:
        head, tail = output.segments()
        return self._redactor.redact_bounded(
            head.decode("utf-8", errors="replace"),
            tail.decode("utf-8", errors="replace"),
            limit_bytes=self._settings.max_output_bytes,
            truncated=output.truncated,
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

    def segments(self) -> tuple[bytes, bytes]:
        return bytes(self._head), bytes(self._tail)


def _safe_parts(path: str) -> tuple[str, ...]:
    normalized = normalize_relative_posix(path)
    if is_sensitive_path(normalized):
        raise PolicyDenied("sensitive paths are unavailable to ordinary tools")
    return PurePosixPath(normalized).parts


def _open_workspace_root(
    canonical_workspace: Path,
    *,
    before_open: Callable[[], None] | None = None,
) -> int:
    """Reopen one canonical absolute directory without following any path component."""
    if not canonical_workspace.is_absolute():
        raise ValueError("canonical workspace must be absolute")
    expected = os.stat(canonical_workspace, follow_symlinks=False)
    if not stat.S_ISDIR(expected.st_mode):
        raise ValueError("workspace must be a directory")
    if before_open is not None:
        before_open()

    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in canonical_workspace.parts[1:]:
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        actual = os.fstat(current_fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("workspace identity changed during secure open")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _move_verified(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
    *,
    expected_source: os.stat_result | None = None,
    expected_destination: os.stat_result | None = None,
    before_rename: Callable[[], None] | None = None,
) -> None:
    """Verify the source leaf around rename and fail closed on identity changes."""
    source_handle = os.open(
        source_name,
        os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=source_fd,
    )
    renamed = False
    try:
        captured = os.fstat(source_handle)
        if stat.S_ISDIR(captured.st_mode):
            raise ValueError("move_file cannot move a directory")
        if expected_source is not None and _file_identity(captured) != _file_identity(
            expected_source
        ):
            raise ValueError("move_file source identity changed before rename")
        if before_rename is not None:
            before_rename()
        current = os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
        if _file_identity(current) != _file_identity(captured):
            raise ValueError("move_file source identity changed before rename")

        try:
            current_destination = os.stat(
                destination_name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if expected_destination is not None:
                raise ValueError("move_file destination identity changed before rename")
        else:
            if expected_destination is None or _file_identity(
                current_destination
            ) != _file_identity(expected_destination):
                raise ValueError("move_file destination identity changed before rename")

        os.replace(
            source_name,
            destination_name,
            src_dir_fd=source_fd,
            dst_dir_fd=destination_fd,
        )
        renamed = True
        destination = os.stat(
            destination_name,
            dir_fd=destination_fd,
            follow_symlinks=False,
        )
        if _file_identity(destination) != _file_identity(captured):
            raise MutationStateUncertain(
                "move_file destination identity changed after rename"
            )
    except MutationStateUncertain:
        raise
    except Exception as error:
        if renamed:
            raise MutationStateUncertain(
                "move_file destination could not be verified after rename"
            ) from error
        raise
    finally:
        try:
            os.close(source_handle)
        except Exception as error:
            if renamed:
                raise MutationStateUncertain(
                    "move_file source handle could not be closed after rename"
                ) from error
            raise


def _file_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _resolve_git_executable(injected: Path | None) -> Path:
    candidates: tuple[Path, ...]
    if injected is not None:
        candidates = (injected,)
    else:
        candidates = (Path("/usr/bin/git"), Path("/bin/git"))
    trusted_parents = {Path("/usr/bin").resolve(), Path("/bin").resolve()}
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            candidate_stat = resolved.stat()
        except OSError:
            continue
        if not stat.S_ISREG(candidate_stat.st_mode) or not os.access(resolved, os.X_OK):
            continue
        if injected is None and resolved.parent not in trusted_parents:
            continue
        return resolved
    raise ValueError("trusted Git executable is unavailable")


def _duration_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
