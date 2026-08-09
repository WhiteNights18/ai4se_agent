"""Literal secret redaction for logs, tool output, and user-visible status."""

from __future__ import annotations


class Redactor:
    """Replace registered literal secrets without retaining displayable metadata."""

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = tuple(sorted({secret for secret in secrets if secret}, key=len, reverse=True))

    def redact(self, value: str) -> str:
        """Return text with every registered secret replaced by a fixed marker."""
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
