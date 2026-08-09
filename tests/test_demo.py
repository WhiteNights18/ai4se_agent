"""Regression coverage for the offline security demonstration."""

from __future__ import annotations

import subprocess
import sys

from guarded_agent import demo


def test_demo_runs_three_offline_scenarios() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "guarded_agent", "demo"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "dangerous action blocked" in result.stdout
    assert "feedback correction passed" in result.stdout
    assert "approval tampering blocked" in result.stdout


def test_demo_does_not_require_sys_executable_to_be_python(monkeypatch) -> None:
    """A frozen executable points sys.executable at the CLI binary itself."""
    monkeypatch.setattr(sys, "executable", "/not/a/python/interpreter")

    assert "feedback correction passed" in demo.run_demo()
