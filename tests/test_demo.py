"""Regression coverage for the offline security demonstration."""

from __future__ import annotations

import subprocess
import sys


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
