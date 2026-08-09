"""Literal secret redaction for logs, tool output, and user-visible status."""

from __future__ import annotations

import re

_MARKER_CANDIDATES = ("[REDACTED]", "<redacted>", "[MASKED]", "***")
_TRUNCATION_MARKER = "\n...\n"


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
        fragments = {
            fragment
            for secret in self._secrets
            for length in range(4, len(secret))
            for fragment in (secret[:length], secret[-length:])
        }
        self._truncated_fragment_pattern = (
            re.compile("|".join(re.escape(fragment) for fragment in sorted(fragments, key=len, reverse=True)))
            if fragments
            else None
        )

    def redact(self, value: str) -> str:
        """Return text with every registered secret replaced in one regex pass."""
        if self._pattern is None:
            return value
        redacted = self._pattern.sub(self._marker, value)
        while True:
            scrubbed = self._pattern.sub("", redacted)
            if scrubbed == redacted:
                return redacted
            redacted = scrubbed

    def redact_truncated(self, value: str) -> str:
        """Redact full secrets and long boundary fragments in retained truncated text.

        Full secrets shorter than four characters are still removed. Partial fragments
        shorter than four characters are intentionally outside this contract.
        """
        redacted = self.redact(value)
        if self._truncated_fragment_pattern is None:
            return redacted
        while True:
            scrubbed = self._truncated_fragment_pattern.sub("", redacted)
            if scrubbed == redacted:
                return redacted
            redacted = scrubbed

    def redact_bounded(
        self,
        head: str,
        tail: str,
        *,
        limit_bytes: int,
        truncated: bool,
    ) -> tuple[str, bool]:
        """Redact retained segments and return UTF-8 output within the exact byte budget."""
        if limit_bytes < 1:
            raise ValueError("limit_bytes must be positive")
        if not truncated:
            redacted = self.redact(head + tail)
            clipped = _utf8_prefix(redacted, limit_bytes)
            return clipped, len(redacted.encode("utf-8")) > limit_bytes

        redacted_head = self.redact_truncated(head)
        redacted_tail = self.redact_truncated(tail)
        marker = _utf8_prefix(_TRUNCATION_MARKER, limit_bytes)
        remaining = limit_bytes - len(marker.encode("utf-8"))
        head_budget = remaining // 2
        tail_budget = remaining - head_budget
        bounded_head = _utf8_prefix(redacted_head, head_budget)
        bounded_tail = _utf8_suffix(redacted_tail, tail_budget)

        unused_head = head_budget - len(bounded_head.encode("utf-8"))
        unused_tail = tail_budget - len(bounded_tail.encode("utf-8"))
        if unused_head:
            bounded_tail = _utf8_suffix(redacted_tail, tail_budget + unused_head)
        if unused_tail:
            bounded_head = _utf8_prefix(redacted_head, head_budget + unused_tail)
        return bounded_head + marker + bounded_tail, True


def _utf8_prefix(value: str, limit_bytes: int) -> str:
    encoded = value.encode("utf-8")
    return encoded[:limit_bytes].decode("utf-8", errors="ignore")


def _utf8_suffix(value: str, limit_bytes: int) -> str:
    if limit_bytes == 0:
        return ""
    encoded = value.encode("utf-8")
    return encoded[-limit_bytes:].decode("utf-8", errors="ignore")
