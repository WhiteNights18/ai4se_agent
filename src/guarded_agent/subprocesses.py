"""Bounded, shell-free subprocess execution."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic, sleep
from typing import BinaryIO, cast

from guarded_agent.redaction import Redactor


class ProcessStatus(str, Enum):
    """The process outcomes callers need to classify deterministically."""

    EXITED = "exited"
    TIMED_OUT = "timed_out"
    START_FAILED = "start_failed"


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: ProcessStatus
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int


class CommandRunner:
    """Execute argv directly with a small, credential-free environment."""

    def __init__(self, *, redactor: Redactor, max_output_bytes: int = 65_536) -> None:
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self._redactor = redactor
        self._max_output_bytes = min(max_output_bytes, 65_536)

    def run(self, argv: list[str], cwd: Path, timeout: float) -> CommandResult:
        started = monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=_allowed_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            return CommandResult(
                ProcessStatus.START_FAILED,
                None,
                "",
                self._redactor.redact(f"command failed to start: {error}"),
                False,
                False,
                _duration_ms(started),
            )
        stdout_buffer = _BoundedBytes(self._max_output_bytes)
        stderr_buffer = _BoundedBytes(self._max_output_bytes)
        status = _capture_until_exit(
            process,
            timeout=timeout,
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
        )
        return CommandResult(
            status,
            process.returncode if status is ProcessStatus.EXITED else None,
            self._redactor.redact(_decode(stdout_buffer.render())),
            self._redactor.redact(_decode(stderr_buffer.render())),
            stdout_buffer.truncated,
            stderr_buffer.truncated,
            _duration_ms(started),
        )


class _BoundedBytes:
    """Keep only a fixed-size head and tail while draining an arbitrary stream."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._head_limit = limit // 2
        self._tail_limit = limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self._total = 0

    @property
    def truncated(self) -> bool:
        return self._total > self._limit

    def add(self, chunk: bytes) -> None:
        self._total += len(chunk)
        head_remaining = self._head_limit - len(self._head)
        if head_remaining:
            self._head.extend(chunk[:head_remaining])
            chunk = chunk[head_remaining:]
        if not chunk:
            return
        self._tail.extend(chunk)
        if len(self._tail) > self._tail_limit:
            del self._tail[: len(self._tail) - self._tail_limit]

    def render(self) -> bytes:
        if not self.truncated:
            return bytes(self._head + self._tail)
        return bytes(self._head) + b"\n... output truncated ...\n" + bytes(self._tail)


def _capture_until_exit(
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
    stdout_buffer: _BoundedBytes,
    stderr_buffer: _BoundedBytes,
) -> ProcessStatus:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("command pipes were not created")
    streams = {
        process.stdout: stdout_buffer,
        process.stderr: stderr_buffer,
    }
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)

    deadline = monotonic() + max(0, timeout)
    status = ProcessStatus.EXITED
    while selector.get_map() or process.poll() is None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            status = ProcessStatus.TIMED_OUT
            _terminate_process_group(process)
            deadline = monotonic() + 1
        events = selector.select(timeout=max(0, min(0.05, deadline - monotonic())))
        for key, _ in events:
            stream = cast(BinaryIO, key.fileobj)
            try:
                chunk = os.read(stream.fileno(), 8192)
            except BlockingIOError:
                continue
            if chunk:
                streams[stream].add(chunk)
            else:
                selector.unregister(stream)
                stream.close()
        if status is ProcessStatus.TIMED_OUT and monotonic() >= deadline:
            for key in list(selector.get_map().values()):
                stream = cast(BinaryIO, key.fileobj)
                selector.unregister(stream)
                stream.close()
            break

    selector.close()
    if process.poll() is None:
        _terminate_process_group(process)
        status = ProcessStatus.TIMED_OUT
    else:
        process.wait()
    return status


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = monotonic() + 0.2
    while _process_group_exists(process.pid) and monotonic() < deadline:
        sleep(0.01)
    if _process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _allowed_environment() -> dict[str, str]:
    allowed_names = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
    return {name: os.environ[name] for name in allowed_names if name in os.environ}


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _duration_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
