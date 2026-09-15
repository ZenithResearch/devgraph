from __future__ import annotations

import inspect
from datetime import datetime, timedelta

from devgraph.auth import (
    SCOPE_READ,
    AuthorityContext,
    CredentialEnvelope,
    CredentialVerifier,
    LocalDevVerifier,
)
from devgraph.model.base import utc_now

AUDIENCE = "devgraph"


def _envelope() -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        scopes=frozenset({SCOPE_READ}),
        expires_at=utc_now() + timedelta(hours=1),
        issuer="devgraph-test-issuer",
        audience=AUDIENCE,
    )


class RecordingVerifier:
    """Fake future-adapter test double proving the seam is adapter-ready.

    Stands in for a future secS/Dregg/macaroon adapter: same call
    surface, returns AuthorityContext, records what it was asked."""

    def __init__(self, envelope: CredentialEnvelope) -> None:
        self._envelope = envelope
        self.calls: list[tuple[str | None, str, datetime | None]] = []

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext:
        self.calls.append((credential, audience, now))
        return AuthorityContext(envelope=self._envelope)


def _verify_through_seam(
    verifier: CredentialVerifier, credential: str | None
) -> AuthorityContext:
    """Call surface a future enforcement layer would use — verifier-agnostic."""
    return verifier.verify(credential, audience=AUDIENCE)


def test_local_dev_verifier_conforms_to_protocol():
    assert isinstance(LocalDevVerifier(auth_mode="local-dev"), CredentialVerifier)


def test_recording_test_double_conforms_to_protocol():
    assert isinstance(RecordingVerifier(_envelope()), CredentialVerifier)


def test_test_double_is_substitutable_through_the_same_call_surface():
    envelope = _envelope()

    local = LocalDevVerifier(auth_mode="local-dev")
    local.register("fake-credential-alpha", envelope)
    double = RecordingVerifier(envelope)

    for verifier in (local, double):
        context = _verify_through_seam(verifier, "fake-credential-alpha")
        assert isinstance(context, AuthorityContext)
        assert context.actor_id == "agent-frank"

    assert double.calls == [("fake-credential-alpha", AUDIENCE, None)]


def test_protocol_signature_has_keyword_only_audience_and_now():
    signature = inspect.signature(CredentialVerifier.verify)
    params = signature.parameters

    assert params["audience"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["now"].default is None

    for implementation in (LocalDevVerifier, RecordingVerifier):
        impl_params = inspect.signature(implementation.verify).parameters
        assert impl_params["audience"].kind is inspect.Parameter.KEYWORD_ONLY
        assert impl_params["now"].kind is inspect.Parameter.KEYWORD_ONLY
        assert impl_params["now"].default is None
