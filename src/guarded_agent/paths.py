"""Deterministic workspace path validation and sensitive-name policy."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path, PurePosixPath


class PolicyDenied(ValueError):
    """Raised when an action violates a non-approvable governance boundary."""


_PRIVATE_KEY_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_PRIVATE_KEY_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def normalize_relative_posix(candidate: str | Path) -> str:
    """Return one validated relative POSIX spelling without resolving the filesystem."""
    raw = candidate.as_posix() if isinstance(candidate, Path) else candidate
    if not isinstance(raw, str) or not raw or "\0" in raw or "\\" in raw:
        raise PolicyDenied("path must be a non-empty relative POSIX path")

    path = PurePosixPath(raw)
    raw_segments = raw.split("/")
    if path.is_absolute() or any(segment in {".", ".."} for segment in raw_segments):
        raise PolicyDenied("path must be a relative POSIX path without dot segments")

    normalized = path.as_posix()
    if normalized == ".":
        raise PolicyDenied("path must identify an entry inside the workspace")
    return normalized


def canonicalize_inside(workspace: Path, candidate: str | Path) -> Path:
    """Resolve a candidate and prove its final real path stays below the workspace."""
    normalized = normalize_relative_posix(candidate)
    try:
        canonical_workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PolicyDenied("workspace cannot be resolved") from error
    if not canonical_workspace.is_dir():
        raise PolicyDenied("workspace must be a directory")

    target = canonical_workspace.joinpath(*PurePosixPath(normalized).parts)
    resolved = _resolve_existing_or_nearest_parent(target)
    try:
        relative = resolved.relative_to(canonical_workspace)
    except ValueError as error:
        raise PolicyDenied("path resolves outside the workspace") from error
    if relative == Path("."):
        raise PolicyDenied("path must resolve strictly inside the workspace")
    return resolved


def is_sensitive_path(candidate: str | Path) -> bool:
    """Return whether ordinary file tools must be denied access to this path."""
    normalized = normalize_relative_posix(candidate)
    parts = PurePosixPath(normalized).parts
    basename = parts[-1]

    if ".git" in parts:
        return True
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename in _PRIVATE_KEY_NAMES or PurePosixPath(basename).suffix in _PRIVATE_KEY_SUFFIXES:
        return True
    return any(
        part == ".guarded-agent" and next_part.startswith("credentials")
        for part, next_part in pairwise(parts)
    )


def _resolve_existing_or_nearest_parent(target: Path) -> Path:
    missing: list[str] = []
    current = target
    while not current.exists():
        if current.is_symlink():
            try:
                current.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise PolicyDenied("path cannot be safely resolved") from error
        missing.append(current.name)
        parent = current.parent
        if parent == current:
            raise PolicyDenied("path has no resolvable parent")
        current = parent

    try:
        resolved_parent = current.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PolicyDenied("path cannot be safely resolved") from error
    if missing and not resolved_parent.is_dir():
        raise PolicyDenied("new path parent must be a directory")
    return resolved_parent.joinpath(*reversed(missing))
