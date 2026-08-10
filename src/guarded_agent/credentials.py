"""Encrypted, local credential storage for real model providers."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Version 1 has no released predecessor, so migration support is intentionally unnecessary.
_VAULT_VERSION = 1
_MAX_VAULT_BYTES = 64 * 1024
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32
_MIN_CIPHERTEXT_BYTES = 16
_MAX_CIPHERTEXT_BYTES = _MAX_VAULT_BYTES
_MAX_PROVIDER_LENGTH = 128
_MAX_ENDPOINT_LENGTH = 2048
_DEFAULT_ENDPOINT = "https://api.openai.com/v1"
_KDF_PARAMETERS: dict[str, str | int] = {
    "name": "scrypt",
    "n": 2**15,
    "r": 8,
    "p": 1,
    "length": _KEY_BYTES,
}
_AAD = json.dumps(
    {"version": _VAULT_VERSION, "kdf": _KDF_PARAMETERS},
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
).encode("ascii")


class CredentialError(ValueError):
    """Raised when a credential cannot be safely stored."""

    def __init__(self) -> None:
        super().__init__("unable to store credentials")


class CredentialUnlockError(CredentialError):
    """Raised when the vault cannot be authenticated and decrypted."""

    def __init__(self) -> None:
        ValueError.__init__(self, "unable to unlock credentials")


@dataclass(frozen=True, slots=True)
class Credential:
    """A decrypted provider credential whose key is deliberately not repr-visible."""

    provider: str
    endpoint: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Non-secret vault state suitable for a CLI, UI, or audit event."""

    provider: str | None
    endpoint: str | None
    configured: bool


@dataclass(frozen=True, slots=True)
class _Envelope:
    salt: bytes
    nonce: bytes
    ciphertext: bytes


class CredentialVault:
    """Stores one provider credential in an authenticated, password-encrypted file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def set(
        self,
        provider: str,
        api_key: str,
        master_password: str,
        *,
        endpoint: str = _DEFAULT_ENDPOINT,
    ) -> None:
        """Encrypt and atomically replace the stored credential."""
        failure: CredentialError | None = None
        try:
            _validate_provider(provider)
            _validate_endpoint(endpoint)
            _require_nonempty(api_key, "api key")
            _require_nonempty(master_password, "master password")
            salt = os.urandom(_SALT_BYTES)
            nonce = os.urandom(_NONCE_BYTES)
            plaintext = json.dumps(
                {"provider": provider, "endpoint": endpoint, "api_key": api_key},
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            ciphertext = AESGCM(_derive_key(master_password, salt)).encrypt(nonce, plaintext, _AAD)
            envelope = {
                "version": _VAULT_VERSION,
                "kdf": _KDF_PARAMETERS,
                "salt": _encode(salt),
                "nonce": _encode(nonce),
                "ciphertext": _encode(ciphertext),
            }
            content = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        except UnicodeError:
            failure = CredentialError()
        if failure is not None:
            raise failure
        if len(content) > _MAX_VAULT_BYTES:
            raise CredentialError()
        self._atomic_write(content)

    def get(self, master_password: str) -> Credential:
        """Return decrypted credentials or a deliberately non-specific unlock error."""
        failure: CredentialUnlockError | None = None
        try:
            _require_nonempty(master_password, "master password")
            envelope = _load_envelope(self._path)
            plaintext = AESGCM(_derive_key(master_password, envelope.salt)).decrypt(
                envelope.nonce, envelope.ciphertext, _AAD
            )
            payload = _load_payload(plaintext)
            return Credential(payload["provider"], payload["endpoint"], payload["api_key"])
        except (InvalidTag, OSError, UnicodeError, ValueError, TypeError, binascii.Error):
            failure = CredentialUnlockError()
        if failure is not None:
            raise failure
        raise AssertionError("credential vault did not return a result")

    def status(self, master_password: str | None = None) -> CredentialStatus:
        """Return only authenticated metadata; a locked vault exposes configuration existence."""
        if not self._path.is_file():
            return CredentialStatus(provider=None, endpoint=None, configured=False)
        if master_password is None:
            return CredentialStatus(provider=None, endpoint=None, configured=True)
        credential = self.get(master_password)
        return CredentialStatus(
            provider=credential.provider, endpoint=credential.endpoint, configured=True
        )

    def clear(self) -> None:
        """Remove the encrypted credential, if configured."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        _sync_directory(self._path.parent)

    def _atomic_write(self, content: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        open_descriptor: int | None = file_descriptor
        try:
            os.fchmod(file_descriptor, stat.S_IRUSR | stat.S_IWUSR)
            temporary_file = os.fdopen(file_descriptor, "wb")
            open_descriptor = None
            with temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
            _sync_directory(self._path.parent)
        except BaseException:
            if open_descriptor is not None:
                try:
                    os.close(open_descriptor)
                except OSError:
                    pass
            temporary_path.unlink(missing_ok=True)
            raise


def _derive_key(master_password: str, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt,
        length=_KEY_BYTES,
        n=int(_KDF_PARAMETERS["n"]),
        r=int(_KDF_PARAMETERS["r"]),
        p=int(_KDF_PARAMETERS["p"]),
    ).derive(master_password.encode("utf-8"))


def _load_envelope(path: Path) -> _Envelope:
    with path.open("rb") as vault_file:
        raw = vault_file.read(_MAX_VAULT_BYTES + 1)
    if len(raw) > _MAX_VAULT_BYTES:
        raise ValueError("vault exceeds maximum size")
    parsed: Any = json.loads(raw, object_pairs_hook=_reject_duplicate_fields)
    if type(parsed) is not dict or set(parsed) != {"version", "kdf", "salt", "nonce", "ciphertext"}:
        raise ValueError("invalid envelope")
    if type(parsed["version"]) is not int or parsed["version"] != _VAULT_VERSION:
        raise ValueError("unsupported envelope version")
    _validate_kdf(parsed["kdf"])
    return _Envelope(
        salt=_decode(parsed["salt"], exact_length=_SALT_BYTES),
        nonce=_decode(parsed["nonce"], exact_length=_NONCE_BYTES),
        ciphertext=_decode(
            parsed["ciphertext"],
            minimum_length=_MIN_CIPHERTEXT_BYTES,
            maximum_length=_MAX_CIPHERTEXT_BYTES,
        ),
    )


def _load_payload(plaintext: bytes) -> dict[str, str]:
    parsed: Any = json.loads(plaintext.decode("utf-8"), object_pairs_hook=_reject_duplicate_fields)
    if type(parsed) is not dict or set(parsed) != {"provider", "endpoint", "api_key"}:
        raise ValueError("invalid credential payload")
    provider = parsed["provider"]
    endpoint = parsed["endpoint"]
    api_key = parsed["api_key"]
    _validate_provider(provider)
    _validate_endpoint(endpoint)
    _require_nonempty(api_key, "api key")
    return {"provider": provider, "endpoint": endpoint, "api_key": api_key}


def _validate_kdf(value: Any) -> None:
    if type(value) is not dict or set(value) != set(_KDF_PARAMETERS):
        raise ValueError("invalid key derivation parameters")
    if not isinstance(value["name"], str):
        raise TypeError("invalid key derivation parameters")
    for parameter in ("n", "r", "p", "length"):
        if type(value[parameter]) is not int:
            raise ValueError("invalid key derivation parameters")
    if value != _KDF_PARAMETERS:
        raise ValueError("unsupported key derivation parameters")


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate envelope field")
        result[key] = value
    return result


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(
    value: Any,
    *,
    exact_length: int | None = None,
    minimum_length: int = 0,
    maximum_length: int = _MAX_VAULT_BYTES,
) -> bytes:
    if not isinstance(value, str) or len(value) > _MAX_VAULT_BYTES:
        raise ValueError("invalid encoded envelope field")
    decoded = base64.b64decode(value.encode("ascii"), validate=True)
    if exact_length is not None and len(decoded) != exact_length:
        raise ValueError("invalid encoded envelope field")
    if not minimum_length <= len(decoded) <= maximum_length:
        raise ValueError("invalid encoded envelope field")
    return decoded


def _require_nonempty(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must not be empty")


def _validate_provider(provider: Any) -> None:
    _require_nonempty(provider, "provider")
    if len(provider) > _MAX_PROVIDER_LENGTH or _has_control_characters(provider):
        raise ValueError("invalid provider")


def _validate_endpoint(endpoint: Any) -> None:
    _require_nonempty(endpoint, "endpoint")
    if (
        len(endpoint) > _MAX_ENDPOINT_LENGTH
        or endpoint != endpoint.strip()
        or not endpoint.isascii()
        or _has_control_characters(endpoint)
        or any(character.isspace() or character == "\\" for character in endpoint)
    ):
        raise ValueError("invalid endpoint")
    try:
        parts = urlsplit(endpoint)
        hostname = parts.hostname
        port = parts.port
    except (UnicodeError, ValueError):
        raise ValueError("invalid endpoint") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or hostname is None
        or not _is_valid_hostname(hostname)
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port is not None and not 1 <= port <= 65535
        or parts.scheme == "http" and not _is_loopback_hostname(hostname)
    ):
        raise ValueError("invalid endpoint")


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        normalized = hostname.removesuffix(".")
        if not normalized or len(normalized) > 253:
            return False
        labels = normalized.split(".")
        return all(
            1 <= len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        )
    return True


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.removesuffix(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
