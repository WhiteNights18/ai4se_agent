from pathlib import Path

import pytest

from guarded_agent.config import ConfigError, load_settings
from guarded_agent.domain import Settings


def test_missing_repository_config_uses_all_defaults(tmp_path: Path) -> None:
    """Catch a loader that leaves a missing config partially uninitialized."""
    settings = load_settings(tmp_path)

    assert settings == Settings(
        max_turns=20,
        max_consecutive_failures=4,
        total_timeout_seconds=1800,
        command_timeout_seconds=120,
        max_output_bytes=65536,
        validation_commands=[],
    )


def test_repository_config_loads_only_the_declared_values(tmp_path: Path) -> None:
    """Catch config parsing that loses configured limits or validator argv boundaries."""
    (tmp_path / "guarded-agent.toml").write_text(
        "[limits]\n"
        "max_turns = 2\n"
        "max_consecutive_failures = 1\n"
        "total_timeout_seconds = 60\n"
        "command_timeout_seconds = 30\n"
        "max_output_bytes = 1024\n"
        "[validation]\n"
        'commands = [["pytest", "-q"], ["ruff", "check", "src"]]\n',
        encoding="utf-8",
    )

    assert load_settings(tmp_path) == Settings(
        max_turns=2,
        max_consecutive_failures=1,
        total_timeout_seconds=60,
        command_timeout_seconds=30,
        max_output_bytes=1024,
        validation_commands=[["pytest", "-q"], ["ruff", "check", "src"]],
    )


def test_repository_config_cannot_disable_hard_boundaries(tmp_path: Path) -> None:
    """Catch a repository config that can alter compiled governance boundaries."""
    (tmp_path / "guarded-agent.toml").write_text(
        "[governance]\nallow_workspace_escape=true\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="^invalid configuration:"):
        load_settings(tmp_path)


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ("[limits]\nmax_turns = 21\n", "max_turns"),
        ("[limits]\nmax_turns = true\n", "max_turns"),
        ("[limits]\nunknown_limit = 2\n", "unknown_limit"),
        ("[validation]\ncommands = [[]]\n", "commands"),
        ("[validation]\ncommands = [[\"pytest\", \"\"]]\n", "commands"),
        ("unexpected = true\n", "unexpected"),
    ],
)
def test_repository_config_rejects_unknown_or_out_of_range_values(
    tmp_path: Path, config: str, reason: str
) -> None:
    """Catch a loader that admits unsafe limits, unknown keys, or empty argv entries."""
    (tmp_path / "guarded-agent.toml").write_text(config, encoding="utf-8")

    with pytest.raises(ConfigError, match=f"(?s)^invalid configuration:.*{reason}"):
        load_settings(tmp_path)


def test_workspace_must_resolve_to_an_existing_directory(tmp_path: Path) -> None:
    """Catch a loader that permits a nonexistent path or regular file as a workspace."""
    with pytest.raises(ConfigError, match="^invalid configuration:"):
        load_settings(tmp_path / "missing")

    file_workspace = tmp_path / "workspace-file"
    file_workspace.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ConfigError, match="^invalid configuration:"):
        load_settings(file_workspace)


def test_non_utf8_repository_config_raises_a_prefixed_config_error(tmp_path: Path) -> None:
    """Catch malformed config bytes that leak a decoder exception past the loader boundary."""
    (tmp_path / "guarded-agent.toml").write_bytes(b"[limits]\nmax_turns = \xff\n")

    with pytest.raises(ConfigError, match="^invalid configuration:"):
        load_settings(tmp_path)
