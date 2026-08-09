from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from guarded_agent.credentials import CredentialError, CredentialUnlockError, CredentialVault


def test_vault_never_writes_plaintext_and_round_trips_credentials(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    vault.set("openai-compatible", "sk-secret-value", "master-pass")

    stored = vault_path.read_bytes()
    assert b"sk-secret-value" not in stored
    assert b"master-pass" not in stored
    credential = vault.get("master-pass")
    assert credential.provider == "openai-compatible"
    assert credential.endpoint == "https://api.openai.com/v1"
    assert credential.api_key == "sk-secret-value"


def test_vault_envelope_uses_fresh_random_values_and_required_scrypt_parameters(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    vault.set("openai-compatible", "first-secret", "master-pass")
    first_envelope = json.loads(vault_path.read_text(encoding="utf-8"))
    vault.set("openai-compatible", "second-secret", "master-pass")
    second_envelope = json.loads(vault_path.read_text(encoding="utf-8"))

    assert first_envelope["version"] == 1
    assert first_envelope["kdf"] == {
        "name": "scrypt",
        "n": 2**15,
        "r": 8,
        "p": 1,
        "length": 32,
    }
    assert len(base64.b64decode(first_envelope["salt"], validate=True)) == 16
    assert len(base64.b64decode(first_envelope["nonce"], validate=True)) == 12
    assert first_envelope["salt"] != second_envelope["salt"]
    assert first_envelope["nonce"] != second_envelope["nonce"]
    assert vault.get("master-pass").api_key == "second-secret"


def test_vault_rejects_wrong_password_with_a_generic_error(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.bin")
    vault.set("openai-compatible", "sk-secret-value", "correct-password")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("wrong-password")


def test_vault_rejects_valid_length_ciphertext_bit_flip_with_generic_error(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")
    envelope = json.loads(vault_path.read_text(encoding="utf-8"))
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"], validate=True))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    vault_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")


def test_vault_rejects_tampered_authenticated_kdf_metadata(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")
    envelope = json.loads(vault_path.read_text(encoding="utf-8"))
    envelope["kdf"]["n"] = 2**14
    vault_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")


def test_vault_status_only_exposes_authenticated_metadata_after_unlock(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)

    assert vault.status().configured is False
    vault.set(
        "openai-compatible",
        "sk-secret-value",
        "master-pass",
        endpoint="https://models.example.test/v1",
    )

    locked_status = CredentialVault(vault_path).status()
    unlocked_status = CredentialVault(vault_path).status("master-pass")
    assert locked_status.configured is True
    assert locked_status.provider is None
    assert locked_status.endpoint is None
    assert unlocked_status.provider == "openai-compatible"
    assert unlocked_status.endpoint == "https://models.example.test/v1"
    assert unlocked_status.configured is True
    assert "sk-secret-value" not in repr(unlocked_status)
    assert "sk-secret-value" not in repr(vault.get("master-pass"))


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://models.example.test/v1",
        "https://user:password@models.example.test/v1",
        "https://models.example.test/v1?token=value",
        "https://models.example.test/v1#section",
        "https://models.example.test/\n",
        "https://" + "a" * 2050,
    ],
)
def test_vault_rejects_unsafe_endpoint_forms(tmp_path: Path, endpoint: str) -> None:
    vault = CredentialVault(tmp_path / "vault.bin")

    with pytest.raises(ValueError, match="endpoint"):
        vault.set("openai-compatible", "sk-secret-value", "master-pass", endpoint=endpoint)


@pytest.mark.parametrize(
    ("api_key", "master_password"),
    [("api-secret-\ud800", "ordinary-password"), ("ordinary-key", "master-secret-\ud800")],
)
def test_vault_never_exposes_secret_bearing_unicode_errors(
    tmp_path: Path, api_key: str, master_password: str
) -> None:
    vault = CredentialVault(tmp_path / "vault.bin")

    with pytest.raises(CredentialError) as captured:
        vault.set("openai-compatible", api_key, master_password)

    error = captured.value
    details = " ".join(
        repr(value)
        for value in (error, error.__cause__, error.__context__, str(error), repr(error))
    )
    assert api_key not in details
    assert master_password not in details


def test_vault_rejects_oversized_and_malformed_envelopes_before_unlocking(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")

    vault_path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")

    malformed = {
        "version": True,
        "kdf": {"name": "scrypt", "n": 2**15, "r": 8, "p": 1, "length": 32},
        "salt": base64.b64encode(os.urandom(15)).decode("ascii"),
        "nonce": base64.b64encode(os.urandom(12)).decode("ascii"),
        "ciphertext": base64.b64encode(os.urandom(16)).decode("ascii"),
    }
    vault_path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")


def test_vault_rejects_duplicate_envelope_fields(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")
    envelope = vault_path.read_text(encoding="utf-8")
    vault_path.write_text(envelope.replace('"version":1', '"version":2,"version":1'), encoding="utf-8")

    with pytest.raises(CredentialUnlockError, match="^unable to unlock credentials$"):
        vault.get("master-pass")


def test_atomic_replace_failure_preserves_old_vault_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "old-secret", "master-pass")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr("guarded_agent.credentials.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replacement failure"):
        vault.set("openai-compatible", "new-secret", "master-pass")

    assert vault.get("master-pass").api_key == "old-secret"
    assert list(tmp_path.glob(".vault.bin.*.tmp")) == []


def test_vault_file_permissions_are_owner_only_and_clear_removes_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault.bin"
    vault = CredentialVault(vault_path)
    vault.set("openai-compatible", "sk-secret-value", "master-pass")

    assert stat.S_IMODE(vault_path.stat().st_mode) == 0o600
    vault.clear()
    assert vault_path.exists() is False
    assert vault.status().configured is False
