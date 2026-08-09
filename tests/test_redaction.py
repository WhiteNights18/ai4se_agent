from __future__ import annotations

import subprocess
import sys

from guarded_agent.redaction import Redactor


def test_redactor_removes_registered_secret() -> None:
    redactor = Redactor(["sk-secret-value"])

    redacted = redactor.redact("failed with sk-secret-value")

    assert "sk-secret-value" not in redacted
    assert redacted == "failed with [REDACTED]"


def test_redactor_removes_every_occurrence_and_prefers_longest_secret() -> None:
    redactor = Redactor(["token", "token-with-suffix"])

    redacted = redactor.redact("token-with-suffix and token and token-with-suffix")

    assert redacted == "[REDACTED] and [REDACTED] and [REDACTED]"


def test_redactor_marker_never_reintroduces_registered_secrets() -> None:
    secrets = ["[REDACTED]", "REDACTED", "other-token"]
    redactor = Redactor(secrets)

    redacted = redactor.redact("[REDACTED] REDACTED other-token")

    assert all(secret not in redacted for secret in secrets)


def test_redactor_removes_secrets_recomposed_across_a_replacement_boundary() -> None:
    secrets = ["secret", "x[REDACTED]y"]
    redactor = Redactor(secrets)

    redacted = redactor.redact("xsecrety")

    assert all(secret not in redacted for secret in secrets)


def test_redactor_ignores_empty_secrets_without_changing_unrelated_text() -> None:
    redactor = Redactor(["", "sk-secret-value"])

    assert redactor.redact("ordinary output") == "ordinary output"


def test_redactor_representation_does_not_expose_registered_secrets() -> None:
    redactor = Redactor(["sk-secret-value"])

    assert "sk-secret-value" not in repr(redactor)


def test_truncated_redaction_removes_long_secret_fragments_but_keeps_short_text() -> None:
    """Catch truncation-boundary fragments of four or more characters remaining visible."""
    redactor = Redactor(["super-secret-token", "key"])

    redacted_head, redacted_tail = redactor.redact_truncated(
        "normal super-se", "et-token normal key"
    )

    assert "super-se" not in redacted_head
    assert "et-token" not in redacted_tail
    assert "key" not in redacted_tail
    assert "normal" in redacted_head
    assert "normal" in redacted_tail


def test_truncated_redaction_repeats_until_repeated_boundary_fragments_are_gone() -> None:
    """A removal can expose another prefix of the same secret at the boundary."""
    redactor = Redactor(["ABCDEFGH"])

    redacted_head, redacted_tail = redactor.redact_truncated("ABCDABCD", "tail")

    assert redacted_head == ""
    assert redacted_tail == "tail"


def test_truncated_redaction_restarts_after_later_secret_exposes_earlier_secret() -> None:
    """Secret ordering cannot leave a newly exposed earlier boundary fragment."""
    redactor = Redactor(["ABCDEFGH9", "WXYZ12"])

    redacted_head, _ = redactor.redact_truncated("ABCDWXYZ", "tail")

    assert redacted_head == ""


def test_truncation_marker_never_exposes_a_registered_marker_secret() -> None:
    """Catch a fixed truncation marker directly inserting a registered secret."""
    redactor = Redactor(["..."])

    output, truncated = redactor.redact_bounded(
        "HEAD", "TAIL", limit_bytes=32, truncated=True
    )

    assert truncated is True
    assert "..." not in output
    assert len(output.encode()) <= 32


def test_marker_adjacency_cannot_recompose_a_registered_secret() -> None:
    """Catch retained text plus the selected marker recomposing a full secret."""
    secret = "A[~]"
    redactor = Redactor([secret])

    output, _ = redactor.redact_bounded(
        "A", "tail", limit_bytes=64, truncated=True
    )

    assert secret not in output


def test_near_limit_secret_uses_bounded_fragment_redaction_memory() -> None:
    """Catch constructing every prefix/suffix fragment for a near-64 KiB secret."""
    program = """
import resource
resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
from guarded_agent.redaction import Redactor
secret = 's' * 65_000
redactor = Redactor([secret])
output, _ = redactor.redact_bounded('HEAD' + secret[:16], secret[-16:] + 'TAIL', limit_bytes=64, truncated=True)
assert secret[:16] not in output
assert secret[-16:] not in output
assert len(output.encode()) <= 64
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
