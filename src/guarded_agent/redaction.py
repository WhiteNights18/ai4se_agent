"""Literal secret redaction for logs, tool output, and user-visible status."""

from __future__ import annotations

import re

_MARKER_CANDIDATES = ("[REDACTED]", "<redacted>", "[MASKED]", "***")
_TRUNCATION_MARKER_CANDIDATES = ("[~]", "[TRUNCATED]", "<truncated>", "[CUT]")


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
        self._truncation_marker = next(
            (
                candidate
                for candidate in _TRUNCATION_MARKER_CANDIDATES
                if all(secret not in candidate for secret in self._secrets)
            ),
            "",
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

    def redact_truncated(self, head: str, tail: str) -> tuple[str, str]:
        """Redact full secrets and long fragments at the two retained boundaries.

        Full secrets shorter than four characters are still removed. Partial fragments
        shorter than four characters are intentionally outside this contract.
        """
        redacted_head = self.redact(head)
        redacted_tail = self.redact(tail)
        while True:
            for secret in self._secrets:
                head_overlap = _longest_prefix_at_end(secret, redacted_head)
                tail_overlap = _longest_prefix_at_end(secret[::-1], redacted_tail[::-1])
                if head_overlap >= 4:
                    redacted_head = redacted_head[:-head_overlap]
                if tail_overlap >= 4:
                    redacted_tail = redacted_tail[tail_overlap:]
                if head_overlap >= 4 or tail_overlap >= 4:
                    break
            else:
                return redacted_head, redacted_tail

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

        redacted_head = self.redact(head)
        redacted_tail = self.redact(tail)
        marker = _utf8_prefix(self._truncation_marker, limit_bytes)
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
        bounded_head, bounded_tail = self.redact_truncated(bounded_head, bounded_tail)
        composed = self.redact(bounded_head + marker + bounded_tail)
        return _utf8_prefix(composed, limit_bytes), True


def _utf8_prefix(value: str, limit_bytes: int) -> str:
    encoded = value.encode("utf-8")
    return encoded[:limit_bytes].decode("utf-8", errors="ignore")


def _utf8_suffix(value: str, limit_bytes: int) -> str:
    if limit_bytes == 0:
        return ""
    encoded = value.encode("utf-8")
    return encoded[-limit_bytes:].decode("utf-8", errors="ignore")


def _longest_prefix_at_end(pattern: str, value: str) -> int:
    """Return the longest pattern prefix that is also a value suffix in linear space."""
    if not pattern or not value:
        return 0
    prefix_lengths = [0] * len(pattern)
    matched = 0
    for index in range(1, len(pattern)):
        while matched and pattern[index] != pattern[matched]:
            matched = prefix_lengths[matched - 1]
        if pattern[index] == pattern[matched]:
            matched += 1
        prefix_lengths[index] = matched

    matched = 0
    for character in value:
        while matched and character != pattern[matched]:
            matched = prefix_lengths[matched - 1]
        if character == pattern[matched]:
            matched += 1
        if matched == len(pattern):
            matched = prefix_lengths[matched - 1]
    return matched
