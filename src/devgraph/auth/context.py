"""Authority context derived from a parsed credential envelope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

    @property
    def credential_id(self) -> str | None:
        return self.envelope.credential_id

    @property
    def credential_version(self) -> int | None:
        return self.envelope.credential_version

    @property
    def read_grants(self) -> tuple[Any, ...]:
        return self.envelope.read_grants

    @property
    def lifecycle_status(self) -> str | None:
        return self.envelope.lifecycle_status

    @property
    def resource(self) -> str | None:
        return self.envelope.resource
