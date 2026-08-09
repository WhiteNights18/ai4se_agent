import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

import guarded_agent.tools as tools_module
from guarded_agent.domain import Action, Settings, ToolName
from guarded_agent.redaction import Redactor
from guarded_agent.subprocesses import CommandRunner, ProcessStatus
from guarded_agent.tools import ToolRegistry


def action(tool: ToolName, **arguments: object) -> Action:
    return Action.model_validate({"tool": tool, "arguments": arguments})


def test_workspace_root_open_rejects_an_injected_identity_swap(tmp_path: Path) -> None:
    """Catch a resolved workspace path being replaced before its root fd is opened."""
    workspace = tmp_path / "workspace"
    displaced = tmp_path / "displaced"
    workspace.mkdir()

    def swap_after_identity_capture() -> None:
        workspace.rename(displaced)
        workspace.mkdir()

    with pytest.raises(ValueError, match="workspace identity changed"):
        tools_module._open_workspace_root(  # type: ignore[attr-defined]
            workspace.resolve(), before_open=swap_after_identity_capture
        )


def test_command_runner_does_not_expand_shell_syntax(tmp_path: Path) -> None:
    """Catch command execution accidentally passing argv through a shell."""
    runner = CommandRunner(redactor=Redactor([]))

    result = runner.run(["printf", "%s", "$(touch owned)"], tmp_path, 2)

    assert not (tmp_path / "owned").exists()
    assert "$(touch owned)" in result.stdout


def test_command_runner_kills_the_process_group_on_timeout(tmp_path: Path) -> None:
    """Catch a timed-out command leaving a child alive to perform a later side effect."""
    sentinel = tmp_path / "child-owned"
    child = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"time.sleep(.35); open({str(sentinel)!r}, 'w').write('owned')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    )
    runner = CommandRunner(redactor=Redactor([]))

    result = runner.run([sys.executable, "-c", parent], tmp_path, 0.1)
    time.sleep(0.45)

    assert result.status is ProcessStatus.TIMED_OUT
    assert not sentinel.exists()


def test_command_runner_does_not_inherit_api_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch ambient API keys crossing the child-process boundary."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-never-in-child")
    runner = CommandRunner(redactor=Redactor(["sk-never-in-child"]))

    result = runner.run(
        [sys.executable, "-c", "import os; print(os.getenv('OPENAI_API_KEY', 'absent'))"],
        tmp_path,
        2,
    )

    assert result.stdout.strip() == "absent"


def test_command_runner_bounds_each_stream_with_head_and_tail(tmp_path: Path) -> None:
    """Catch unbounded capture or truncation that drops the diagnostically useful tail."""
    program = (
        "import sys; "
        "sys.stdout.write('OUT-HEAD-' + 'x' * 1000 + '-OUT-TAIL'); "
        "sys.stderr.write('ERR-HEAD-' + 'y' * 1000 + '-ERR-TAIL')"
    )
    runner = CommandRunner(redactor=Redactor([]), max_output_bytes=64)

    result = runner.run([sys.executable, "-c", program], tmp_path, 2)

    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.stdout.startswith("OUT-HEAD-")
    assert result.stdout.endswith("-OUT-TAIL")
    assert result.stderr.startswith("ERR-HEAD-")
    assert result.stderr.endswith("-ERR-TAIL")
    assert len(result.stdout.encode()) <= 64
    assert len(result.stderr.encode()) <= 64


def test_command_runner_redacts_secret_prefix_at_truncated_head(tmp_path: Path) -> None:
    """Catch a secret prefix leaking where the retained head meets omitted output."""
    secret = "super-secret-token"
    exposed_prefix = secret[:8]
    output = "A" * 24 + exposed_prefix + "M" * 200 + "TAIL"
    runner = CommandRunner(redactor=Redactor([secret]), max_output_bytes=64)

    result = runner.run([sys.executable, "-c", f"print({output!r}, end='')"], tmp_path, 2)

    assert result.stdout_truncated is True
    assert exposed_prefix not in result.stdout
    assert secret not in result.stdout
    assert len(result.stdout.encode()) <= 64


def test_command_runner_redacts_secret_suffix_at_truncated_tail(tmp_path: Path) -> None:
    """Catch a secret suffix leaking where omitted output meets the retained tail."""
    secret = "super-secret-token"
    exposed_suffix = secret[-8:]
    output = "HEAD" + "M" * 200 + exposed_suffix + "B" * 24
    runner = CommandRunner(redactor=Redactor([secret]), max_output_bytes=64)

    result = runner.run([sys.executable, "-c", f"print({output!r}, end='')"], tmp_path, 2)

    assert result.stdout_truncated is True
    assert exposed_suffix not in result.stdout
    assert secret not in result.stdout
    assert len(result.stdout.encode()) <= 64


def test_command_runner_redacts_both_streams(tmp_path: Path) -> None:
    """Catch credentials being exposed through either captured stream."""
    secret = "sk-output-secret"
    runner = CommandRunner(redactor=Redactor([secret]))
    program = f"import sys; print({secret!r}); print({secret!r}, file=sys.stderr)"

    result = runner.run([sys.executable, "-c", program], tmp_path, 2)

    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "[REDACTED]" in result.stdout
    assert "[REDACTED]" in result.stderr


def test_command_runner_distinguishes_start_failure(tmp_path: Path) -> None:
    """Catch executable lookup failure being confused with a program exit."""
    runner = CommandRunner(redactor=Redactor([]))

    result = runner.run(["definitely-not-a-real-guarded-agent-command"], tmp_path, 2)

    assert result.status is ProcessStatus.START_FAILED
    assert result.exit_code is None
    assert "failed to start" in result.stderr


@pytest.mark.parametrize("argv", [[], ["bad\x00executable"]])
def test_command_runner_structures_invalid_argv_start_failures(
    tmp_path: Path, argv: list[str]
) -> None:
    """Catch Python argv validation errors escaping the structured runner boundary."""
    runner = CommandRunner(redactor=Redactor([]))

    result = runner.run(argv, tmp_path, 2)

    assert result.status is ProcessStatus.START_FAILED
    assert result.exit_code is None
    assert "failed to start" in result.stderr


def test_registry_rejects_extra_arguments_before_writing(tmp_path: Path) -> None:
    """Catch undocumented fields crossing the public parse boundary before execution."""

    with pytest.raises(ValidationError):
        action(ToolName.WRITE_FILE, path="owned.txt", content="bad", surprise=True)

    assert not (tmp_path / "owned.txt").exists()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (ToolName.LIST_DIRECTORY, {"path": "src"}),
        (ToolName.READ_FILE, {"path": "file.txt"}),
        (ToolName.SEARCH_TEXT, {"path": "src", "query": "x"}),
        (ToolName.WRITE_FILE, {"path": "file.txt", "content": "x"}),
        (ToolName.DELETE_FILE, {"path": "file.txt"}),
        (ToolName.MOVE_FILE, {"source": "a", "destination": "b"}),
        (ToolName.GIT_STATUS, {}),
        (ToolName.GIT_DIFF, {}),
        (ToolName.RUN_COMMAND, {"argv": ["true"]}),
        (ToolName.RUN_VALIDATOR, {"argv": ["true"]}),
        (ToolName.SAVE_MEMORY, {"category": "fact", "content": "x"}),
        (ToolName.RETRIEVE_MEMORY, {"query": "x"}),
        (ToolName.COMPLETE, {}),
        (ToolName.CANNOT_CONTINUE, {"reason": "blocked"}),
    ],
)
def test_every_tool_argument_model_forbids_extra_fields(
    tmp_path: Path,
    tool: ToolName,
    arguments: dict[str, object],
) -> None:
    """Catch any public action variant silently accepting an undocumented field."""

    with pytest.raises(ValidationError):
        action(tool, **arguments, undocumented="smuggled")


def test_registry_writes_and_reads_a_file_atomically(tmp_path: Path) -> None:
    """Catch a write handler failing to publish the complete content as one replacement."""
    target = tmp_path / "result.txt"
    target.write_text("old", encoding="utf-8")
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        written = registry.execute(
            action(ToolName.WRITE_FILE, path="result.txt", content="complete new value")
        )
        read = registry.execute(action(ToolName.READ_FILE, path="result.txt"))

    assert written.exit_code == 0
    assert written.changes == ["result.txt"]
    assert read.exit_code == 0
    assert read.stdout == "complete new value"
    assert target.read_text(encoding="utf-8") == "complete new value"
    assert not any(path.name.startswith(".guarded-agent-write-") for path in tmp_path.iterdir())


def test_atomic_write_failure_preserves_old_content_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a failed publish damaging the prior file or leaving a writable temp artifact."""
    target = tmp_path / "result.txt"
    target.write_text("old", encoding="utf-8")
    real_replace = tools_module.os.replace

    def fail_publish(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if source.startswith(".guarded-agent-write-"):
            raise OSError("injected publish failure")
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(tools_module.os, "replace", fail_publish)
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(ToolName.WRITE_FILE, path="result.txt", content="new")
        )

    assert result.exit_code is None
    assert target.read_text(encoding="utf-8") == "old"
    assert not any(path.name.startswith(".guarded-agent-write-") for path in tmp_path.iterdir())


def test_atomic_overwrite_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Catch atomic replacement silently changing repository file permissions."""
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o750)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(ToolName.WRITE_FILE, path="script.sh", content="new")
        )

    assert result.exit_code == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_registry_never_follows_a_workspace_symlink_for_file_access(tmp_path: Path) -> None:
    """Catch an execution-time symlink swap escaping a prior canonical-path decision."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside-secret", encoding="utf-8")
    (workspace / "swapped").symlink_to(outside, target_is_directory=True)
    with ToolRegistry(workspace, Settings(), Redactor([])) as registry:
        read = registry.execute(action(ToolName.READ_FILE, path="swapped/secret.txt"))
        write = registry.execute(
            action(ToolName.WRITE_FILE, path="swapped/owned.txt", content="owned")
        )

    assert read.exit_code is None
    assert write.exit_code is None
    assert "outside-secret" not in read.stdout
    assert not (outside / "owned.txt").exists()


def test_registry_safely_reopens_an_internal_file_symlink(tmp_path: Path) -> None:
    """Catch safe internal symlinks being rejected instead of reopened by canonical identity."""
    target = tmp_path / "real.txt"
    target.write_text("internal content", encoding="utf-8")
    (tmp_path / "alias.txt").symlink_to(target.name)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(action(ToolName.READ_FILE, path="alias.txt"))

    assert result.exit_code == 0
    assert result.stdout == "internal content"


def test_registry_safely_reopens_an_internal_search_symlink(tmp_path: Path) -> None:
    """Catch search_text losing SPEC-required internal directory symlink support."""
    target = tmp_path / "real-src"
    target.mkdir()
    (target / "inside.py").write_text("find needle\n", encoding="utf-8")
    (tmp_path / "alias-src").symlink_to(target.name, target_is_directory=True)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(ToolName.SEARCH_TEXT, path="alias-src", query="needle")
        )

    assert result.exit_code == 0
    assert result.stdout == "alias-src/inside.py:1:find needle"


def test_registry_writes_through_internal_directory_and_leaf_symlinks(tmp_path: Path) -> None:
    """Internal aliases name canonical mutation targets, including a new leaf."""
    real = tmp_path / "real"
    real.mkdir()
    target = real / "existing.txt"
    target.write_text("old", encoding="utf-8")
    directory_alias = tmp_path / "alias-dir"
    leaf_alias = tmp_path / "alias-file"
    directory_alias.symlink_to(real.name, target_is_directory=True)
    leaf_alias.symlink_to(target.relative_to(tmp_path))

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        new_result = registry.execute(
            action(ToolName.WRITE_FILE, path="alias-dir/new.txt", content="new")
        )
        existing_result = registry.execute(
            action(ToolName.WRITE_FILE, path="alias-file", content="updated")
        )

    assert new_result.exit_code == 0
    assert existing_result.exit_code == 0
    assert (real / "new.txt").read_text(encoding="utf-8") == "new"
    assert target.read_text(encoding="utf-8") == "updated"
    assert directory_alias.is_symlink()
    assert leaf_alias.is_symlink()


def test_registry_deletes_and_moves_through_internal_symlinks(tmp_path: Path) -> None:
    """Delete and move act on resolved targets while preserving alias entries."""
    real = tmp_path / "real"
    archive = tmp_path / "archive"
    real.mkdir()
    archive.mkdir()
    delete_target = real / "delete.txt"
    move_target = real / "move.txt"
    delete_target.write_text("delete", encoding="utf-8")
    move_target.write_text("move", encoding="utf-8")
    delete_alias = tmp_path / "delete-alias"
    move_alias = tmp_path / "move-alias"
    archive_alias = tmp_path / "archive-alias"
    delete_alias.symlink_to(delete_target.relative_to(tmp_path))
    move_alias.symlink_to(move_target.relative_to(tmp_path))
    archive_alias.symlink_to(archive.name, target_is_directory=True)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        deleted = registry.execute(action(ToolName.DELETE_FILE, path="delete-alias"))
        moved = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="move-alias",
                destination="archive-alias/moved.txt",
            )
        )

    assert deleted.exit_code == 0
    assert moved.exit_code == 0
    assert not delete_target.exists()
    assert delete_alias.is_symlink()
    assert not move_target.exists()
    assert (archive / "moved.txt").read_text(encoding="utf-8") == "move"
    assert move_alias.is_symlink()
    assert archive_alias.is_symlink()


def test_registry_denies_all_mutations_through_external_symlinks(tmp_path: Path) -> None:
    """Canonical mutation resolution must never escape the held workspace root."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    (workspace / "outside-dir").symlink_to(outside, target_is_directory=True)
    (workspace / "outside-file").symlink_to(outside_file)
    local = workspace / "local.txt"
    local.write_text("local", encoding="utf-8")

    with ToolRegistry(workspace, Settings(), Redactor([])) as registry:
        write = registry.execute(
            action(ToolName.WRITE_FILE, path="outside-dir/new.txt", content="bad")
        )
        delete = registry.execute(action(ToolName.DELETE_FILE, path="outside-file"))
        move_source = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="outside-file",
                destination="stolen.txt",
            )
        )
        move_destination = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="local.txt",
                destination="outside-dir/moved.txt",
            )
        )

    assert write.exit_code is None
    assert delete.exit_code is None
    assert move_source.exit_code is None
    assert move_destination.exit_code is None
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert not (outside / "new.txt").exists()
    assert not (outside / "moved.txt").exists()
    assert local.read_text(encoding="utf-8") == "local"


def test_registry_rejects_fifo_reads_without_blocking(tmp_path: Path) -> None:
    """Catch read_file blocking on a FIFO before it can reject the non-regular target."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    def unblock_unsafe_reader() -> None:
        time.sleep(0.3)
        try:
            descriptor = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        os.close(descriptor)

    release = threading.Thread(target=unblock_unsafe_reader)
    release.start()
    started = time.monotonic()
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(action(ToolName.READ_FILE, path="pipe"))
    elapsed = time.monotonic() - started
    release.join(timeout=1)

    assert elapsed < 0.2
    assert result.exit_code is None
    assert "regular file" in result.stderr


def test_registry_delete_and_move_operate_on_dirfd_entries(tmp_path: Path) -> None:
    """Catch destructive handlers reopening an already-approved path through resolution."""
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    (tmp_path / "delete.txt").write_text("delete", encoding="utf-8")
    (tmp_path / "archive").mkdir()
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        moved = registry.execute(
            action(ToolName.MOVE_FILE, source="old.txt", destination="archive/new.txt")
        )
        deleted = registry.execute(action(ToolName.DELETE_FILE, path="delete.txt"))

    assert moved.exit_code == 0
    assert moved.changes == ["old.txt", "archive/new.txt"]
    assert deleted.exit_code == 0
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "archive/new.txt").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "delete.txt").exists()


def test_verified_move_rejects_an_injected_source_leaf_replacement(tmp_path: Path) -> None:
    """Catch move_file renaming a different leaf than the one whose type was approved."""
    source = tmp_path / "source.txt"
    displaced = tmp_path / "displaced.txt"
    replacement = tmp_path / "replacement.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("original", encoding="utf-8")
    replacement.write_text("attacker", encoding="utf-8")
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def replace_source_leaf() -> None:
        source.rename(displaced)
        replacement.rename(source)

    try:
        with pytest.raises(ValueError, match="source identity changed"):
            tools_module._move_verified(  # type: ignore[attr-defined]
                directory_fd,
                source.name,
                directory_fd,
                destination.name,
                before_rename=replace_source_leaf,
            )
    finally:
        os.close(directory_fd)

    assert source.read_text(encoding="utf-8") == "attacker"
    assert displaced.read_text(encoding="utf-8") == "original"
    assert not destination.exists()


def test_move_post_rename_mismatch_reports_uncertain_changes_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch post-rename failure moving an attacker leaf back or claiming no changes."""
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    displaced = tmp_path / "moved-original.txt"
    attacker = tmp_path / "attacker.txt"
    source.write_text("original", encoding="utf-8")
    attacker.write_text("attacker", encoding="utf-8")
    real_stat = tools_module.os.stat
    replaced = False

    def stat_with_post_rename_replacement(
        path: str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        if (
            path == destination.name
            and dir_fd is not None
            and not replaced
            and destination.exists()
            and not source.exists()
        ):
            destination.rename(displaced)
            attacker.rename(destination)
            replaced = True
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(tools_module.os, "stat", stat_with_post_rename_replacement)
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="source.txt",
                destination="destination.txt",
            )
        )

    assert result.exit_code is None
    assert result.stderr.startswith("state_uncertain:")
    assert result.changes == ["source.txt", "destination.txt"]
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "attacker"
    assert displaced.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("failing_fsync_call", [1, 2])
def test_move_post_rename_fsync_failure_reports_uncertain_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_fsync_call: int,
) -> None:
    """Every parent fsync failure after rename must report the changed paths."""
    source_directory = tmp_path / "source"
    destination_directory = tmp_path / "destination"
    source_directory.mkdir()
    destination_directory.mkdir()
    source = source_directory / "file.txt"
    destination = destination_directory / "moved.txt"
    source.write_text("content", encoding="utf-8")
    real_fsync = tools_module.os.fsync
    calls = 0

    def fail_selected_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failing_fsync_call:
            raise OSError("injected parent fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(tools_module.os, "fsync", fail_selected_fsync)
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="source/file.txt",
                destination="destination/moved.txt",
            )
        )

    assert calls == failing_fsync_call
    assert result.exit_code is None
    assert result.stderr.startswith("state_uncertain:")
    assert result.changes == ["source/file.txt", "destination/moved.txt"]
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "content"


def test_move_any_post_rename_operation_failure_reports_uncertain_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source-handle close is also inside the post-rename uncertainty boundary."""
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("content", encoding="utf-8")
    real_close = tools_module.os.close
    injected = False

    def fail_first_close_after_rename(descriptor: int) -> None:
        nonlocal injected
        if not injected and destination.exists() and not source.exists():
            injected = True
            real_close(descriptor)
            raise OSError("injected post-rename close failure")
        real_close(descriptor)

    monkeypatch.setattr(tools_module.os, "close", fail_first_close_after_rename)
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(
                ToolName.MOVE_FILE,
                source="source.txt",
                destination="destination.txt",
            )
        )

    assert injected is True
    assert result.exit_code is None
    assert result.stderr.startswith("state_uncertain:")
    assert result.changes == ["source.txt", "destination.txt"]


def test_registry_bounds_reads_and_redacts_file_content(tmp_path: Path) -> None:
    """Catch a dedicated read returning an unbounded or credential-bearing payload."""
    secret = "sk-file-secret"
    (tmp_path / "large.txt").write_text("HEAD-" + secret + "x" * 1000 + "-TAIL")
    with ToolRegistry(
        tmp_path,
        Settings(max_output_bytes=64),
        Redactor([secret]),
    ) as registry:
        result = registry.execute(action(ToolName.READ_FILE, path="large.txt"))

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("HEAD-")
    assert result.stdout.endswith("-TAIL")
    assert secret not in result.stdout
    assert len(result.stdout.encode()) <= 64


def test_registry_lists_and_searches_without_following_symlinks(tmp_path: Path) -> None:
    """Catch recursive search traversing a symlink or returning nondeterministic matches."""
    source = tmp_path / "src"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (source / "a.py").write_text("first needle\n", encoding="utf-8")
    (source / "b.py").write_text("second needle\n", encoding="utf-8")
    (outside / "secret.py").write_text("outside needle\n", encoding="utf-8")
    (source / "escape").symlink_to(outside, target_is_directory=True)
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        listing = registry.execute(action(ToolName.LIST_DIRECTORY, path="src"))
        searched = registry.execute(
            action(ToolName.SEARCH_TEXT, path="src", query="needle", max_results=10)
        )

    assert listing.stdout.splitlines() == ["a.py", "b.py", "escape"]
    assert searched.stdout.splitlines() == [
        "src/a.py:1:first needle",
        "src/b.py:1:second needle",
    ]
    assert "secret.py" not in searched.stdout


def test_registry_bounds_large_directory_listings(tmp_path: Path) -> None:
    """Catch list_directory bypassing the same result-size bound as other read tools."""
    directory = tmp_path / "many"
    directory.mkdir()
    for index in range(20):
        (directory / f"entry-{index:02}.txt").touch()
    with ToolRegistry(tmp_path, Settings(max_output_bytes=32), Redactor([])) as registry:
        result = registry.execute(action(ToolName.LIST_DIRECTORY, path="many"))

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("entry-00")
    assert result.stdout.endswith("entry-19.txt")
    assert len(result.stdout.encode()) <= 32


def test_search_rejects_a_total_tree_entry_limit_without_partial_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch per-directory/file counters allowing a wide tree to bypass the global cap."""
    root = tmp_path / "tree"
    root.mkdir()
    for name in ("a", "b"):
        child = root / name
        child.mkdir()
        (child / "match.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(tools_module, "_MAX_DIRECTORY_ENTRIES", 3)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(ToolName.SEARCH_TEXT, path="tree", query="needle")
        )

    assert result.exit_code is None
    assert result.stdout == ""
    assert "entry limit exceeded" in result.stderr


def test_search_rejects_excessive_directory_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch recursive traversal consuming unbounded descriptors or call depth."""
    nested = tmp_path / "tree" / "one" / "two"
    nested.mkdir(parents=True)
    (nested / "match.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(tools_module, "_MAX_SEARCH_DEPTH", 1, raising=False)

    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        result = registry.execute(
            action(ToolName.SEARCH_TEXT, path="tree", query="needle")
        )

    assert result.exit_code is None
    assert result.stdout == ""
    assert "depth limit exceeded" in result.stderr


def test_registry_runs_only_the_startup_validator_argv(tmp_path: Path) -> None:
    """Catch run_validator accepting an LLM-modified command after startup."""
    sentinel = tmp_path / "validated"
    script = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ok')"
    configured = [sys.executable, "-c", script]
    settings = Settings(validation_commands=[configured])
    with ToolRegistry(tmp_path, settings, Redactor([])) as registry:
        settings.validation_commands.append([sys.executable, "-c", "print('changed')"])
        denied = registry.execute(
            action(ToolName.RUN_VALIDATOR, argv=configured + ["unexpected"])
        )
        allowed = registry.execute(action(ToolName.RUN_VALIDATOR, argv=configured))

    assert denied.exit_code is None
    assert "not configured" in denied.stderr
    assert allowed.exit_code == 0
    assert sentinel.read_text(encoding="utf-8") == "ok"


def test_registry_provides_dedicated_git_status_and_diff(tmp_path: Path) -> None:
    """Catch Git reads being implemented as an arbitrary model-supplied command."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=True,
    )
    tracked.write_text("after\n", encoding="utf-8")
    with ToolRegistry(tmp_path, Settings(), Redactor([])) as registry:
        status = registry.execute(action(ToolName.GIT_STATUS))
        diff = registry.execute(action(ToolName.GIT_DIFF))

    assert status.exit_code == 0
    assert "tracked.txt" in status.stdout
    assert diff.exit_code == 0
    assert "-before" in diff.stdout
    assert "+after" in diff.stdout


def test_dedicated_git_uses_a_startup_resolved_path_and_hardened_configuration(
    tmp_path: Path,
) -> None:
    """Catch dedicated Git reads falling back to PATH or repository-controlled helpers."""
    fake_git = tmp_path / "test-git"
    fake_git.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], "
        "'nosystem': os.environ.get('GIT_CONFIG_NOSYSTEM')}))\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    with ToolRegistry(
        tmp_path,
        Settings(),
        Redactor([]),
        git_executable=fake_git,
    ) as registry:
        result = registry.execute(action(ToolName.GIT_STATUS))

    payload = json.loads(result.stdout)
    assert payload == {
        "argv": [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "status",
            "--short",
        ],
        "nosystem": "1",
    }
