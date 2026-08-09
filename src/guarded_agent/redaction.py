"""Literal secret redaction for logs, tool output, and user-visible status."""

from __future__ import annotations

import re

_MARKER_CANDIDATES = ("[REDACTED]", "<redacted>", "[MASKED]", "***")


class Redactor:
    """Replace registered literal secrets without retaining displayable metadata."""

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = tuple(sorted({secret for secret in secrets if secret}, key=len, reverse=True))
        self._pattern = (
            re.compile("|".join(re.escape(secret) for secret in self._secrets))
            if self._secrets
            else None
        )
        self._marker = next(
            (candidate for candidate in _MARKER_CANDIDATES if all(secret not in candidate for secret in self._secrets)),
            "",
        )

    def redact(self, value: str) -> str:
        """Return text with every registered secret replaced in one regex pass."""
        if self._pattern is None:
            return value
        return self._pattern.sub(self._marker, value)
