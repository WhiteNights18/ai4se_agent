import sys
from pathlib import Path

from guarded_agent.domain import FeedbackKind
from guarded_agent.feedback import FeedbackEngine
from guarded_agent.redaction import Redactor
from guarded_agent.subprocesses import CommandRunner


def test_feedback_classifies_test_failure(tmp_path: Path) -> None:
    """Catch a non-zero validator exit being mislabeled as an infrastructure failure."""
    command = [sys.executable, "-c", "import sys; print('assert x'); sys.exit(1)"]
    feedback = FeedbackEngine(
        CommandRunner(redactor=Redactor([])), configured_commands=[command]
    )

    result = feedback.verify([command], tmp_path)

    assert result.kind is FeedbackKind.TEST_FAILURE


def test_feedback_classifies_all_commands_passing(tmp_path: Path) -> None:
    """Catch successful validators producing anything but final PASS feedback."""
    command = [sys.executable, "-c", "print('ok')"]
    feedback = FeedbackEngine(
        CommandRunner(redactor=Redactor([])), configured_commands=[command]
    )

    result = feedback.verify([command], tmp_path)

    assert result.kind is FeedbackKind.PASS
    assert result.command_result is None


def test_feedback_classifies_timeout_separately(tmp_path: Path) -> None:
    """Catch timeout being collapsed into a test assertion failure."""
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    feedback = FeedbackEngine(
        CommandRunner(redactor=Redactor([])), configured_commands=[command]
    )

    result = feedback.verify([command], tmp_path, timeout=0.05)

    assert result.kind is FeedbackKind.TIMEOUT
    assert result.command_result is not None
    assert result.command_result.exit_code is None


def test_feedback_classifies_start_failure_as_tool_failure(tmp_path: Path) -> None:
    """Catch validator startup failures being reported as failing tests."""
    command = ["definitely-not-a-real-validator"]
    feedback = FeedbackEngine(
        CommandRunner(redactor=Redactor([])), configured_commands=[command]
    )

    result = feedback.verify([command], tmp_path)

    assert result.kind is FeedbackKind.TOOL_FAILURE


def test_feedback_rejects_commands_added_after_its_startup_snapshot(tmp_path: Path) -> None:
    """Catch persisted acceptance data adding arbitrary validator argv at verification time."""
    sentinel = tmp_path / "not-validated"
    configured = [[sys.executable, "-c", "print('configured')"]]
    injected = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(sentinel)!r}).write_text('owned')",
    ]
    feedback = FeedbackEngine(
        CommandRunner(redactor=Redactor([])),
        configured_commands=configured,
    )
    configured.append(injected)

    result = feedback.verify([injected], tmp_path)

    assert result.kind is FeedbackKind.POLICY_VIOLATION
    assert result.command_result is None
    assert not sentinel.exists()
