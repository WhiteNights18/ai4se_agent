from __future__ import annotations

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

    redacted = redactor.redact_truncated("super-se normal et-token key")

    assert "super-se" not in redacted
    assert "et-token" not in redacted
    assert "key" not in redacted
    assert "normal" in redacted
