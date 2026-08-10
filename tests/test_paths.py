from pathlib import Path

import pytest

from guarded_agent.paths import PolicyDenied, canonicalize_inside, is_sensitive_path


@pytest.mark.parametrize(
    "candidate",
    ["", ".", "../secret.txt", "src/../secret.txt", "/etc/passwd", "src\\file.py", "bad\0name"],
)
def test_non_relative_posix_paths_are_denied(tmp_path: Path, candidate: str) -> None:
    """Catch path syntax that can change meaning before workspace enforcement."""
    with pytest.raises(PolicyDenied, match="path"):
        canonicalize_inside(tmp_path, candidate)


def test_symlink_escape_is_denied(tmp_path: Path) -> None:
    """Catch an in-workspace name that resolves to a file outside the workspace."""
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside)

    with pytest.raises(PolicyDenied, match="workspace"):
        canonicalize_inside(tmp_path, Path("link"))


def test_internal_symlink_and_new_descendant_resolve_inside_workspace(tmp_path: Path) -> None:
    """Catch a fence that rejects safe symlinks or fails to resolve a new child through one."""
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "link").symlink_to(target, target_is_directory=True)

    assert canonicalize_inside(tmp_path, Path("link")) == target.resolve(strict=True)
    assert canonicalize_inside(tmp_path, Path("link/new/file.py")) == target / "new/file.py"


def test_broken_symlink_is_denied_instead_of_treated_as_a_new_path(tmp_path: Path) -> None:
    """Catch a broken link whose eventual target could escape after policy evaluation."""
    (tmp_path / "link").symlink_to(tmp_path.parent / "not-created-yet")

    with pytest.raises(PolicyDenied, match="resolve"):
        canonicalize_inside(tmp_path, "link/file.py")


def test_candidate_must_resolve_strictly_below_a_real_workspace(tmp_path: Path) -> None:
    """Catch non-directory workspaces and candidates resolving to the workspace root itself."""
    file_workspace = tmp_path / "workspace-file"
    file_workspace.write_text("x", encoding="utf-8")

    with pytest.raises(PolicyDenied, match="workspace"):
        canonicalize_inside(file_workspace, "child")
    with pytest.raises(PolicyDenied, match="path"):
        canonicalize_inside(tmp_path, ".")


@pytest.mark.parametrize(
    "candidate",
    [
        ".git/config",
        "nested/.git/HEAD",
        ".env",
        "settings/.env.local",
        "keys/id_rsa",
        "keys/id_ed25519",
        "keys/server.pem",
        "keys/server.key",
        "keys/server.p12",
        "keys/server.pfx",
        ".guarded-agent/credentials",
        ".guarded-agent/credentials.sqlite3",
        ".guarded-agent/credentials/vault",
    ],
)
def test_sensitive_path_names_are_detected(candidate: str) -> None:
    """Catch ordinary file tools gaining access to repository or credential material."""
    assert is_sensitive_path(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    ["git/config", ".ENV", "keys/ID_RSA", "keys/server.PEM", ".guarded-agent/cache/credentials"],
)
def test_sensitive_path_matching_is_case_sensitive_and_segment_aware(candidate: str) -> None:
    """Catch broad semantic matching that denies names outside the exact compiled policy."""
    assert is_sensitive_path(candidate) is False
