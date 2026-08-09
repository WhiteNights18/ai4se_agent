"""Encrypted, local credential storage for real model providers."""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_VERSION = 1
_AAD = b"guarded-agent-credential-vault-v1"
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32
_SCRYPT_N = 2**14


class CredentialUnlockError(ValueError):
    """Raised when the vault cannot be authenticated and decrypted."""

    def __init__(self) -> None:
        super().__init__("unable to unlock credentials")


@dataclass(frozen=True, slots=True)
class Credential:
    """A decrypted provider credential whose key is deliberately not repr-visible."""

    provider: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Non-secret vault state suitable for a CLI, UI, or audit event."""

    provider: str | None
    configured: bool


class CredentialVault:
    """Stores one provider credential in an authenticated, password-encrypted file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def set(self, provider: str, api_key: str, master_password: str) -> None:
        """Encrypt and atomically replace the stored credential."""
        _require_nonempty(provider, "provider")
        _require_nonempty(api_key, "api key")
        _require_nonempty(master_password, "master password")
        salt = os.urandom(_SALT_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        plaintext = json.dumps(
            {"provider": provider, "api_key": api_key},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        ciphertext = AESGCM(_derive_key(master_password, salt)).encrypt(nonce, plaintext, _AAD)
        envelope = {
            "version": _VERSION,
            "provider": provider,
            "salt": _encode(salt),
            "nonce": _encode(nonce),
            "ciphertext": _encode(ciphertext),
        }
        self._atomic_write(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))

    def get(self, master_password: str) -> Credential:
        """Return decrypted credentials or a deliberately non-specific unlock error."""
        try:
            _require_nonempty(master_password, "master password")
            envelope = _load_envelope(self._path)
            plaintext = AESGCM(_derive_key(master_password, _decode(envelope["salt"]))).decrypt(
                _decode(envelope["nonce"]), _decode(envelope["ciphertext"]), _AAD
            )
            payload = _load_payload(plaintext)
            if payload["provider"] != envelope["provider"]:
                raise ValueError("provider mismatch")
            return Credential(payload["provider"], payload["api_key"])
        except (InvalidTag, OSError, UnicodeError, ValueError, TypeError, KeyError, binascii.Error):
            raise CredentialUnlockError() from None

    def status(self) -> CredentialStatus:
        """Return provider metadata without decrypting or exposing the API key."""
        if not self._path.is_file():
            return CredentialStatus(provider=None, configured=False)
        try:
            envelope = _load_envelope(self._path)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, binascii.Error):
            return CredentialStatus(provider=None, configured=True)
        return CredentialStatus(provider=envelope["provider"], configured=True)

    def clear(self) -> None:
        """Remove the encrypted credential, if configured."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    def _atomic_write(self, content: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(file_descriptor, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
            _sync_directory(self._path.parent)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


def _derive_key(master_password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=_KEY_BYTES, n=_SCRYPT_N, r=8, p=1).derive(
        master_password.encode("utf-8")
    )


def _load_envelope(path: Path) -> dict[str, str]:
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or set(parsed) != {
        "version",
        "provider",
        "salt",
        "nonce",
        "ciphertext",
    }:
        raise ValueError("invalid envelope")
    if parsed["version"] != _VERSION:
        raise ValueError("unsupported envelope version")
    for field_name in ("provider", "salt", "nonce", "ciphertext"):
        if not isinstance(parsed[field_name], str) or not parsed[field_name]:
            raise ValueError("invalid envelope field")
    return {field_name: parsed[field_name] for field_name in ("provider", "salt", "nonce", "ciphertext")}


def _load_payload(plaintext: bytes) -> dict[str, str]:
    parsed: Any = json.loads(plaintext.decode("utf-8"))
    if not isinstance(parsed, dict) or set(parsed) != {"provider", "api_key"}:
        raise ValueError("invalid credential payload")
    for field_name in ("provider", "api_key"):
        if not isinstance(parsed[field_name], str) or not parsed[field_name]:
            raise ValueError("invalid credential payload")
    return {"provider": parsed["provider"], "api_key": parsed["api_key"]}


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _require_nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must not be empty")


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
