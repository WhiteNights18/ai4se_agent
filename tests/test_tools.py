import subprocess
import sys
import time
from pathlib import Path

import pytest

from guarded_agent.domain import Action, Settings, ToolName
from guarded_agent.redaction import Redactor
from guarded_agent.subprocesses import CommandRunner, ProcessStatus
from guarded_agent.tools import ToolRegistry


def action(tool: ToolName, **arguments: object) -> Action:
    return Action.model_validate({"tool": tool, "arguments": arguments})


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
    assert len(result.stdout.encode()) <= 96
    assert len(result.stderr.encode()) <= 96


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


def test_registry_rejects_extra_arguments_before_writing(tmp_path: Path) -> None:
    """Catch undocumented fields reaching a handler before strict validation."""
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

    result = registry.execute(
        action(ToolName.WRITE_FILE, path="owned.txt", content="bad", surprise=True)
    )

    assert result.exit_code is None
    assert "invalid action" in result.stderr
    assert not (tmp_path / "owned.txt").exists()


def test_registry_bounds_and_redacts_validation_errors(tmp_path: Path) -> None:
    """Catch rejected arguments leaking large credential-bearing values in diagnostics."""
    secret = "sk-invalid-action-secret"
    registry = ToolRegistry(
        tmp_path,
        Settings(max_output_bytes=64),
        Redactor([secret]),
    )

    result = registry.execute(
        action(ToolName.WRITE_FILE, path="owned.txt", content="bad", surprise=secret * 100)
    )

    assert result.exit_code is None
    assert result.stderr_truncated is True
    assert secret not in result.stderr
    assert len(result.stderr.encode()) <= 96


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
    """Catch any one dispatch variant silently accepting an undocumented field."""
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

    result = registry.execute(action(tool, **arguments, undocumented="smuggled"))

    assert result.exit_code is None
    assert "invalid action" in result.stderr


def test_registry_writes_and_reads_a_file_atomically(tmp_path: Path) -> None:
    """Catch a write handler failing to publish the complete content as one replacement."""
    target = tmp_path / "result.txt"
    target.write_text("old", encoding="utf-8")
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

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


def test_registry_never_follows_a_workspace_symlink_for_file_access(tmp_path: Path) -> None:
    """Catch an execution-time symlink swap escaping a prior canonical-path decision."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside-secret", encoding="utf-8")
    (workspace / "swapped").symlink_to(outside, target_is_directory=True)
    registry = ToolRegistry(workspace, Settings(), Redactor([]))

    read = registry.execute(action(ToolName.READ_FILE, path="swapped/secret.txt"))
    write = registry.execute(
        action(ToolName.WRITE_FILE, path="swapped/owned.txt", content="owned")
    )

    assert read.exit_code is None
    assert write.exit_code is None
    assert "outside-secret" not in read.stdout
    assert not (outside / "owned.txt").exists()


def test_registry_delete_and_move_operate_on_dirfd_entries(tmp_path: Path) -> None:
    """Catch destructive handlers reopening an already-approved path through resolution."""
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    (tmp_path / "delete.txt").write_text("delete", encoding="utf-8")
    (tmp_path / "archive").mkdir()
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

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


def test_registry_bounds_reads_and_redacts_file_content(tmp_path: Path) -> None:
    """Catch a dedicated read returning an unbounded or credential-bearing payload."""
    secret = "sk-file-secret"
    (tmp_path / "large.txt").write_text("HEAD-" + secret + "x" * 1000 + "-TAIL")
    registry = ToolRegistry(
        tmp_path,
        Settings(max_output_bytes=64),
        Redactor([secret]),
    )

    result = registry.execute(action(ToolName.READ_FILE, path="large.txt"))

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("HEAD-")
    assert result.stdout.endswith("-TAIL")
    assert secret not in result.stdout


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
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

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
    registry = ToolRegistry(tmp_path, Settings(max_output_bytes=32), Redactor([]))

    result = registry.execute(action(ToolName.LIST_DIRECTORY, path="many"))

    assert result.exit_code == 0
    assert result.stdout_truncated is True
    assert result.stdout.startswith("entry-00")
    assert result.stdout.endswith("entry-19.txt")


def test_registry_runs_only_the_startup_validator_argv(tmp_path: Path) -> None:
    """Catch run_validator accepting an LLM-modified command after startup."""
    sentinel = tmp_path / "validated"
    script = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ok')"
    configured = [sys.executable, "-c", script]
    settings = Settings(validation_commands=[configured])
    registry = ToolRegistry(tmp_path, settings, Redactor([]))
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
    registry = ToolRegistry(tmp_path, Settings(), Redactor([]))

    status = registry.execute(action(ToolName.GIT_STATUS))
    diff = registry.execute(action(ToolName.GIT_DIFF))

    assert status.exit_code == 0
    assert "tracked.txt" in status.stdout
    assert diff.exit_code == 0
    assert "-before" in diff.stdout
    assert "+after" in diff.stdout
