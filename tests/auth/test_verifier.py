from __future__ import annotations

from datetime import timedelta

import pytest

from devgraph.auth import (
    SCOPE_READ,
    SCOPE_WRITE,
    AuthError,
    AuthorityContext,
    CredentialEnvelope,
    LocalDevVerifier,
    UnauthenticatedError,
)
from devgraph.model.base import utc_now

FAKE_CREDENTIAL = "fake-credential-alpha"
AUDIENCE = "devgraph"


def _envelope(**overrides) -> CredentialEnvelope:
    fields = {
        "actor_id": "agent-frank",
        "session_id": "session-0001",
        "correlation_id": "corr-0001",
        "scopes": frozenset({SCOPE_READ, SCOPE_WRITE}),
        "expires_at": utc_now() + timedelta(hours=1),
        "issuer": "devgraph-test-issuer",
        "audience": AUDIENCE,
    }
    fields.update(overrides)
    return CredentialEnvelope(**fields)


def _local_dev_verifier() -> LocalDevVerifier:
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(FAKE_CREDENTIAL, _envelope())
    return verifier


@pytest.mark.parametrize(
    "auth_mode",
    [None, "", "production", "LOCAL-DEV", "local_dev", "dev"],
    ids=["unset", "empty", "production", "wrong-case", "underscore", "other"],
)
def test_fail_closed_mode_denies_every_verify_even_registered_credential(auth_mode):
    verifier = LocalDevVerifier(auth_mode=auth_mode)
    verifier.register(FAKE_CREDENTIAL, _envelope())

    with pytest.raises(UnauthenticatedError) as exc_info:
        verifier.verify(FAKE_CREDENTIAL, audience=AUDIENCE)

    assert exc_info.value.category == "unauthenticated"
    assert "unavailable" in str(exc_info.value)


def test_from_env_reads_devgraph_auth_mode(monkeypatch):
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "local-dev")
    verifier = LocalDevVerifier.from_env()
    verifier.register(FAKE_CREDENTIAL, _envelope())

    context = verifier.verify(FAKE_CREDENTIAL, audience=AUDIENCE)

    assert isinstance(context, AuthorityContext)


def test_from_env_defaults_to_fail_closed_when_unset(monkeypatch):
    monkeypatch.delenv("DEVGRAPH_AUTH_MODE", raising=False)
    verifier = LocalDevVerifier.from_env()
    verifier.register(FAKE_CREDENTIAL, _envelope())

    with pytest.raises(UnauthenticatedError):
        verifier.verify(FAKE_CREDENTIAL, audience=AUDIENCE)


def test_local_dev_happy_path_returns_authority_context_with_claims_intact():
    verifier = _local_dev_verifier()

    context = verifier.verify(FAKE_CREDENTIAL, audience=AUDIENCE)

    assert isinstance(context, AuthorityContext)
    assert context.actor_id == "agent-frank"
    assert context.session_id == "session-0001"
    assert context.correlation_id == "corr-0001"
    assert context.scopes == frozenset({SCOPE_READ, SCOPE_WRITE})


@pytest.mark.parametrize("credential", [None, ""], ids=["none", "empty"])
def test_missing_credential_raises_unauthenticated(credential):
    verifier = _local_dev_verifier()

    with pytest.raises(UnauthenticatedError) as exc_info:
        verifier.verify(credential, audience=AUDIENCE)

    assert exc_info.value.category == "unauthenticated"
    assert "required" in str(exc_info.value)


def test_unknown_credential_raises_unauthenticated_without_echoing_it():
    verifier = _local_dev_verifier()
    unknown = "fake-credential-unknown"

    with pytest.raises(UnauthenticatedError) as exc_info:
        verifier.verify(unknown, audience=AUDIENCE)

    assert exc_info.value.category == "unauthenticated"
    assert unknown not in str(exc_info.value)
    assert unknown not in repr(exc_info.value)


def test_expired_credential_raises_unauthenticated():
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(
        FAKE_CREDENTIAL,
        _envelope(expires_at=utc_now() - timedelta(seconds=1)),
    )

    with pytest.raises(UnauthenticatedError) as exc_info:
        verifier.verify(FAKE_CREDENTIAL, audience=AUDIENCE)

    assert exc_info.value.category == "unauthenticated"
    assert "expired" in str(exc_info.value)


def test_expiry_uses_explicit_now_when_provided():
    expires_at = utc_now() + timedelta(hours=1)
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(FAKE_CREDENTIAL, _envelope(expires_at=expires_at))

    context = verifier.verify(
        FAKE_CREDENTIAL,
        audience=AUDIENCE,
        now=expires_at - timedelta(minutes=1),
    )
    assert isinstance(context, AuthorityContext)

    with pytest.raises(UnauthenticatedError):
        verifier.verify(
            FAKE_CREDENTIAL,
            audience=AUDIENCE,
            now=expires_at + timedelta(minutes=1),
        )


def test_audience_mismatch_raises_unauthenticated():
    verifier = _local_dev_verifier()

    with pytest.raises(UnauthenticatedError) as exc_info:
        verifier.verify(FAKE_CREDENTIAL, audience="other-service")

    assert exc_info.value.category == "unauthenticated"
    assert "audience" in str(exc_info.value)


def test_unauthenticated_error_is_auth_error_with_category():
    error = UnauthenticatedError("credential required")

    assert isinstance(error, AuthError)
    assert error.category == "unauthenticated"
    assert error.correlation_id is None


def test_error_can_carry_safe_correlation_id():
    error = UnauthenticatedError("credential expired", correlation_id="corr-0001")

    assert error.correlation_id == "corr-0001"


def test_denial_messages_and_reprs_never_contain_the_credential_string():
    verifier = _local_dev_verifier()
    denial_calls = [
        lambda: verifier.verify(FAKE_CREDENTIAL, audience="other-service"),
        lambda: verifier.verify("fake-credential-unknown", audience=AUDIENCE),
        lambda: LocalDevVerifier(auth_mode=None).verify(
            FAKE_CREDENTIAL, audience=AUDIENCE
        ),
    ]

    for call in denial_calls:
        with pytest.raises(UnauthenticatedError) as exc_info:
            call()
        for rendered in (str(exc_info.value), repr(exc_info.value)):
            assert FAKE_CREDENTIAL not in rendered
            assert "fake-credential-unknown" not in rendered
