"""Literal secret redaction for logs, tool output, and user-visible status."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable

_MARKER_CANDIDATES = ("[REDACTED]", "<redacted>", "[MASKED]", "***")
_TRUNCATION_MARKER_CANDIDATES = ("[~]", "[TRUNCATED]", "<truncated>", "[CUT]")
_MIN_FRAGMENT_LENGTH = 4


class _BoundaryCensor:
    """Remove terminal secret prefixes from a text suffix in amortized linear time."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self._transitions: list[dict[str, int]] = [{}]
        self._failures = [0]
        self._depths = [0]
        for pattern in patterns:
            if len(pattern) < _MIN_FRAGMENT_LENGTH:
                continue
            state = 0
            for character in pattern:
                next_state = self._transitions[state].get(character)
                if next_state is None:
                    next_state = len(self._transitions)
                    self._transitions[state][character] = next_state
                    self._transitions.append({})
                    self._failures.append(0)
                    self._depths.append(self._depths[state] + 1)
                state = next_state
        self._build_failure_links()

    def _build_failure_links(self) -> None:
        pending = deque(self._transitions[0].values())
        while pending:
            state = pending.popleft()
            for character, next_state in self._transitions[state].items():
                fallback = self._failures[state]
                while fallback and character not in self._transitions[fallback]:
                    fallback = self._failures[fallback]
                self._failures[next_state] = self._transitions[fallback].get(character, 0)
                pending.append(next_state)

    def censor_suffix(self, value: str) -> str:
        """Remove secret-prefix suffixes to a fixed point without rescanning text."""
        characters: list[str] = []
        states = [0]
        for character in value:
            characters.append(character)
            states.append(self._advance(states[-1], character))

        while self._depths[states[-1]] >= _MIN_FRAGMENT_LENGTH:
            match_length = self._depths[states[-1]]
            del characters[-match_length:]
            del states[-match_length:]
        return "".join(characters)

    def _advance(self, state: int, character: str) -> int:
        while state and character not in self._transitions[state]:
            state = self._failures[state]
        return self._transitions[state].get(character, 0)


class Redactor:
    """Replace registered literal secrets without retaining displayable metadata."""

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = tuple(sorted({secret for secret in secrets if secret}, key=len, reverse=True))
        self._pattern = (
            re.compile("|".join(re.escape(secret) for secret in self._secrets))
            if self._secrets
            else None
        )
        self._head_censor = _BoundaryCensor(self._secrets)
        self._tail_censor = _BoundaryCensor(secret[::-1] for secret in self._secrets)
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
        redacted_head = self._head_censor.censor_suffix(redacted_head)
        redacted_tail = self._tail_censor.censor_suffix(redacted_tail[::-1])[::-1]
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
