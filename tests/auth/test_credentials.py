from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from devgraph.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    AuthorityContext,
    CredentialEnvelope,
)
from devgraph.model.base import utc_now


def _envelope(**overrides) -> CredentialEnvelope:
    fields = {
        "actor_id": "agent-frank",
        "session_id": "session-0001",
        "correlation_id": "corr-0001",
        "scopes": frozenset({SCOPE_READ, SCOPE_WRITE}),
        "expires_at": utc_now() + timedelta(hours=1),
        "issuer": "devgraph-test-issuer",
        "audience": "devgraph",
    }
    fields.update(overrides)
    return CredentialEnvelope(**fields)


def test_envelope_construction_carries_all_claim_fields():
    expires_at = utc_now() + timedelta(hours=1)
    envelope = _envelope(
        expires_at=expires_at,
        redaction_partitions=("internal", "client-safe"),
    )

    assert envelope.actor_id == "agent-frank"
    assert envelope.session_id == "session-0001"
    assert envelope.correlation_id == "corr-0001"
    assert envelope.scopes == frozenset({SCOPE_READ, SCOPE_WRITE})
    assert envelope.expires_at == expires_at
    assert envelope.issuer == "devgraph-test-issuer"
    assert envelope.audience == "devgraph"
    assert envelope.redaction_partitions == ("internal", "client-safe")


def test_redaction_partitions_default_to_empty_tuple():
    assert _envelope().redaction_partitions == ()


def test_has_scope_reports_granted_and_absent_scopes():
    envelope = _envelope(scopes=frozenset({SCOPE_READ}))

    assert envelope.has_scope(SCOPE_READ) is True
    assert envelope.has_scope(SCOPE_WRITE) is False
    assert envelope.has_scope(SCOPE_ADMIN) is False


def test_is_expired_boundary_at_exact_expiry_instant():
    now = utc_now()
    envelope = _envelope(expires_at=now)

    assert envelope.is_expired(now) is True
    assert envelope.is_expired(now - timedelta(seconds=1)) is False
    assert envelope.is_expired(now + timedelta(seconds=1)) is True


def test_unknown_scope_is_rejected_naming_only_the_scope():
    with pytest.raises(ValueError) as exc_info:
        _envelope(scopes=frozenset({SCOPE_READ, "devgraph.unknown"}))

    message = str(exc_info.value)
    assert "devgraph.unknown" in message
    # Scope names are not secrets, but claim values must not leak.
    assert "agent-frank" not in message
    assert "session-0001" not in message


def test_envelope_is_frozen():
    envelope = _envelope()

    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.actor_id = "someone-else"  # type: ignore[misc]


def test_authority_context_delegates_to_envelope():
    envelope = _envelope(scopes=frozenset({SCOPE_READ}))
    context = AuthorityContext(envelope=envelope)

    assert context.envelope is envelope
    assert context.actor_id == envelope.actor_id
    assert context.session_id == envelope.session_id
    assert context.correlation_id == envelope.correlation_id
    assert context.scopes == envelope.scopes


def test_authority_context_is_frozen():
    context = AuthorityContext(envelope=_envelope())

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.envelope = _envelope()  # type: ignore[misc]


def test_envelope_and_context_have_no_field_that_could_hold_a_credential_string():
    envelope_fields = {f.name for f in dataclasses.fields(CredentialEnvelope)}
    context_fields = {f.name for f in dataclasses.fields(AuthorityContext)}

    for forbidden in ("token", "credential", "secret"):
        assert forbidden not in envelope_fields
        assert forbidden not in context_fields
        assert not hasattr(_envelope(), forbidden)
        assert not hasattr(AuthorityContext(envelope=_envelope()), forbidden)


def test_repr_and_str_carry_only_parsed_claims():
    fake_credential = "fake-credential-alpha"
    envelope = _envelope()
    context = AuthorityContext(envelope=envelope)

    for rendered in (repr(envelope), str(envelope), repr(context), str(context)):
        assert fake_credential not in rendered
        assert "agent-frank" in rendered
