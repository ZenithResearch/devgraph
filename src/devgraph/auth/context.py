"""Authority context derived from a parsed credential envelope."""

from __future__ import annotations

from dataclasses import dataclass

from devgraph.auth.credentials import CredentialEnvelope


@dataclass(frozen=True)
class AuthorityContext:
    """Per-call authority carrying the envelope plus audit accessors."""

    envelope: CredentialEnvelope

    @property
    def actor_id(self) -> str:
        return self.envelope.actor_id

    @property
    def session_id(self) -> str:
        return self.envelope.session_id

    @property
    def correlation_id(self) -> str:
        return self.envelope.correlation_id

    @property
    def scopes(self) -> frozenset[str]:
        return self.envelope.scopes
