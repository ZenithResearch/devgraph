"""Environment-gated local/dev credential verifier fixture.

Deny-by-default: unless the auth mode is exactly ``local-dev``, every
verify call fails closed with a safe unauthenticated error — the
production-like posture. There is deliberately no trusted-localhost
bypass: nothing here inspects hostnames, IPs, or network interfaces.

devgraph does not mint canonical identity. This verifier never
fabricates envelopes; only explicitly registered test/dev fixtures
verify, and fixtures use obviously-fake credential strings only
(e.g. ``fake-credential-alpha``). Error messages never echo the
credential value.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Protocol, runtime_checkable

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.errors import UnauthenticatedError
from devgraph.model.base import utc_now

AUTH_MODE_ENV_VAR = "DEVGRAPH_AUTH_MODE"
AUTH_MODE_LOCAL_DEV = "local-dev"


@runtime_checkable
class CredentialVerifier(Protocol):
    """Credential-verification seam for the generic HTTP API.

    The production local host may install a machine-local read verifier whose
    sole scope is ``devgraph.read``. Exact secS operations use operation-specific
    local receivers and do not turn this seam into a writable bearer channel.
    Implementations verify an opaque credential string for an audience and
    return an :class:`AuthorityContext`, or raise a safe
    :class:`~devgraph.auth.errors.UnauthenticatedError`.
    """

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext: ...


class LocalDevVerifier:
    """In-memory registry verifier enabled only when auth mode is
    exactly ``local-dev``; every other mode fails closed."""

    def __init__(self, auth_mode: str | None) -> None:
        self._auth_mode = auth_mode
        self._registry: dict[str, CredentialEnvelope] = {}

    @classmethod
    def from_env(cls) -> LocalDevVerifier:
        return cls(auth_mode=os.environ.get(AUTH_MODE_ENV_VAR))

    def register(self, credential: str, envelope: CredentialEnvelope) -> None:
        """Wire a fake dev/test credential fixture to a parsed envelope."""
        self._registry[credential] = envelope

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext:
        if self._auth_mode != AUTH_MODE_LOCAL_DEV:
            raise UnauthenticatedError(
                "credential verification unavailable in this mode"
            )
        if not credential:
            raise UnauthenticatedError("credential required")
        envelope = self._registry.get(credential)
        if envelope is None:
            raise UnauthenticatedError("credential not recognized")
        if envelope.is_expired(now if now is not None else utc_now()):
            raise UnauthenticatedError("credential expired")
        if envelope.audience != audience:
            raise UnauthenticatedError("credential audience mismatch")
        return AuthorityContext(envelope=envelope)
