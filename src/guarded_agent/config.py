"""Repository-local settings loading with an intentionally small TOML schema."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from guarded_agent.domain import Settings


class ConfigError(ValueError):
    """Raised when a workspace path or its guarded-agent.toml is invalid."""


_CONFIG_FILE_NAME = "guarded-agent.toml"
_ALLOWED_TABLES = {"limits", "validation"}
_ALLOWED_KEYS = {
    "limits": {
        "max_turns",
        "max_consecutive_failures",
        "total_timeout_seconds",
        "command_timeout_seconds",
        "max_output_bytes",
    },
    "validation": {"commands"},
}


def load_settings(workspace: Path) -> Settings:
    """Load validated settings from the canonical workspace's optional TOML file."""
    canonical_workspace = _resolve_workspace(workspace)
    config_path = canonical_workspace / _CONFIG_FILE_NAME
    if not config_path.exists():
        return Settings()
    if not config_path.is_file():
        raise _invalid("guarded-agent.toml must be a regular file")

    try:
        with config_path.open("rb") as config_file:
            parsed = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise _invalid(str(error)) from error

    _validate_schema(parsed)
    values: dict[str, Any] = {}
    values.update(parsed.get("limits", {}))
    validation = parsed.get("validation", {})
    if "commands" in validation:
        values["validation_commands"] = validation["commands"]

    try:
        return Settings.model_validate(values)
    except ValidationError as error:
        raise _invalid(str(error)) from error


def _resolve_workspace(workspace: Path) -> Path:
    try:
        canonical_workspace = workspace.resolve(strict=True)
    except OSError as error:
        raise _invalid(f"workspace cannot be resolved: {workspace}") from error
    if not canonical_workspace.is_dir():
        raise _invalid("workspace must be a directory")
    return canonical_workspace


def _validate_schema(parsed: dict[str, Any]) -> None:
    unknown_tables = set(parsed) - _ALLOWED_TABLES
    if unknown_tables:
        unknown_table = min(unknown_tables)
        raise _invalid(f"unknown table or key: {unknown_table}")

    for table in _ALLOWED_TABLES:
        values = parsed.get(table, {})
        if not isinstance(values, dict):
            raise _invalid(f"{table} must be a table")
        unknown_keys = set(values) - _ALLOWED_KEYS[table]
        if unknown_keys:
            unknown_key = min(unknown_keys)
            raise _invalid(f"unknown {table} key: {unknown_key}")


def _invalid(reason: str) -> ConfigError:
    return ConfigError(f"invalid configuration: {reason}")
