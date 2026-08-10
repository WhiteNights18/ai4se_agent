"""Contracts for the checked-in Linux binary build and GitLab pipeline."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gitlab_config() -> dict[str, object]:
    config_path = ROOT / ".gitlab-ci.yml"
    assert config_path.is_file(), "GitLab CI configuration must be checked in"

    import yaml

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    return config


def _github_config() -> dict[str, object]:
    config_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert config_path.is_file(), "GitHub Actions workflow must be checked in"

    import yaml

    config = yaml.load(config_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(config, dict)
    return config


def test_gitlab_has_required_python312_unit_test_job() -> None:
    config = _gitlab_config()
    job = config.get("unit-test")
    assert isinstance(job, dict)
    assert job.get("image") == "python:3.12"
    script = job.get("script")
    assert isinstance(script, list)
    assert "make test" in script


def test_gitlab_builds_and_smoke_tests_artifact() -> None:
    config = _gitlab_config()
    job = config.get("build-binary")
    assert isinstance(job, dict)
    script = job.get("script")
    assert isinstance(script, list)
    assert "make binary" in script
    assert "./dist/guarded-agent version" in script
    assert "./dist/guarded-agent demo" in script
    artifacts = job.get("artifacts")
    assert isinstance(artifacts, dict)
    assert artifacts.get("paths") == ["dist/guarded-agent"]


def test_checked_in_spec_embeds_web_resources_and_version_metadata() -> None:
    spec_path = ROOT / "guarded-agent.spec"
    assert spec_path.is_file(), "PyInstaller spec must be checked in"
    spec = spec_path.read_text(encoding="utf-8")
    assert '"templates/*.html"' in spec
    assert '"static/*"' in spec
    assert 'copy_metadata("guarded-agent")' in spec
    assert 'name="guarded-agent"' in spec


def test_build_script_and_make_targets_expose_reproducible_binary_build() -> None:
    script_path = ROOT / "scripts" / "build_binary.sh"
    assert script_path.is_file(), "binary build script must be checked in"
    assert os.access(script_path, os.X_OK)
    script = script_path.read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    assert "PyInstaller" in script

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "quality:" in makefile
    assert "binary:" in makefile


def test_github_runs_tests_and_publishes_smoke_tested_binary() -> None:
    config = _github_config()
    assert "pull_request" in config["on"]
    assert "push" in config["on"]
    assert config["on"]["push"] == {}

    jobs = config["jobs"]
    assert isinstance(jobs, dict)
    unit_test = jobs["unit-test"]
    build_binary = jobs["build-binary"]

    for job in (unit_test, build_binary):
        assert job["runs-on"] == "ubuntu-latest"
        setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
        assert setup["with"]["python-version"] == "3.12"

    assert any(step.get("run") == "make test" for step in unit_test["steps"])
    assert any(step.get("run") == "make quality" for step in unit_test["steps"])
    assert any(step.get("run") == "make binary" for step in build_binary["steps"])
    assert any(step.get("run") == "./dist/guarded-agent version" for step in build_binary["steps"])
    assert any(step.get("run") == "./dist/guarded-agent demo" for step in build_binary["steps"])

    upload = next(step for step in build_binary["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"] == {
        "name": "guarded-agent-linux-x86_64",
        "path": "dist/guarded-agent",
    }
