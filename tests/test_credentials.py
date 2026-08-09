from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from guarded_agent.credentials import CredentialUnlockError, CredentialVault


def test_vault_never_writes_plaintext_and_round_trips_credentials(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    vault.set("openai-compatible", "sk-secret-value", "master-pass")

    assert b"sk-secret-value" not in vault_path.read_bytes()
    credential = vault.get("master-pass")
    assert credential.provider == "openai-compatible"
    assert credential.api_key == "sk-secret-value"


def test_vault_uses_a_versioned_envelope_with_fresh_salt_for_each_update(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    vault.set("openai-compatible", "first-secret", "master-pass")
    first_envelope = json.loads(vault_path.read_text(encoding="utf-8"))
    vault.set("openai-compatible", "second-secret", "master-pass")
    second_envelope = json.loads(vault_path.read_text(encoding="utf-8"))

    assert first_envelope["version"] == 1
    assert second_envelope["version"] == 1
    assert first_envelope["salt"] != second_envelope["salt"]
    assert vault.get("master-pass").api_key == "second-secret"


def test_vault_rejects_wrong_password_with_a_generic_error(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.bin")
    vault.set("openai-compatible", "sk-secret-value", "correct-password")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("wrong-password")


def test_vault_rejects_tampered_envelope_with_same_generic_error(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")
    envelope = json.loads(vault_path.read_text(encoding="utf-8"))
    envelope["ciphertext"] = "AAAA"
    vault_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")


def test_vault_status_masks_secret_and_clear_removes_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    assert vault.status().configured is False
    vault.set("openai-compatible", "sk-secret-value", "master-pass")

    status = vault.status()
    assert status.provider == "openai-compatible"
    assert status.configured is True
    assert "sk-secret-value" not in repr(status)
    assert "sk-secret-value" not in repr(vault.get("master-pass"))

    vault.clear()

    assert vault_path.exists() is False
    assert vault.status().configured is False


def test_vault_file_permissions_are_owner_only(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    CredentialVault(vault_path).set("openai-compatible", "sk-secret-value", "master-pass")

    assert stat.S_IMODE(vault_path.stat().st_mode) == 0o600
