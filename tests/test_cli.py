"""End-to-end coverage for the public command-line surface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "guarded_agent", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_version_reports_package_version() -> None:
    result = _cli("version")

    assert result.returncode == 0
    assert "guarded-agent 0.1.0" in result.stdout


def test_run_rejects_a_missing_workspace() -> None:
    result = _cli("run", "--workspace", "/definitely/missing/guarded-agent-workspace", "--goal", "x")

    assert result.returncode != 0
    assert "workspace" in result.stderr.casefold()


def test_credential_status_does_not_require_unlocking(tmp_path: Path) -> None:
    result = _cli("credential", "status", "--vault", str(tmp_path / "vault.bin"))

    assert result.returncode == 0
    assert "not configured" in result.stdout
