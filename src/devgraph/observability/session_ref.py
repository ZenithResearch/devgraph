"""Source-owned session identity; independent of Work and importing authority.

Existing HermesSessionRef bytes and storage remain readable without migration.
New sessions use one storage label and an extensible application discriminator.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from devgraph.model.validation import validate_work_object_id
from devgraph.observability.hermes_session_ref import HermesSessionRef

SESSION_REF_LABEL = "SessionRef"
SESSION_LINK_LABEL = "SessionLink"
APPLICATION_TYPES = {
    "hermes": "HermesSessionRef",
    "codex": "CodexSessionRef",
    "claude-code": "ClaudeSessionRef",
    "grok": "GrokSessionRef",
}
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,255}")
_APPLICATION = re.compile(r"[a-z][a-z0-9-]{0,63}")
_AUTHORITY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:/-]{0,511}")


def opaque(value: object) -> str:
    """Identifiers only: never a path, URL, title, command, or arbitrary payload."""
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        raise ValueError("invalid_session_identifier")
    return value


def timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("session_timestamp_requires_timezone")
    return value.astimezone(timezone.utc).isoformat()


def digest_id(prefix: str, values: object) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return prefix + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SessionIdentity:
    application: str
    source_instance_id: str
    external_session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.application, str) or not _APPLICATION.fullmatch(self.application):
            raise ValueError("invalid_session_application")
        opaque(self.source_instance_id)
        opaque(self.external_session_id)

    @property
    def id(self) -> str:
        return digest_id("sr-", asdict(self))

    @property
    def ref_type(self) -> str:
        return APPLICATION_TYPES.get(self.application, SESSION_REF_LABEL)


@dataclass(frozen=True)
class SessionRef:
    identity: SessionIdentity
    actor_id: str
    session_id: str
    correlation_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.identity, SessionIdentity):
            raise ValueError("invalid_session_identity")
        for value in (self.actor_id, self.session_id, self.correlation_id):
            if not isinstance(value, str) or not _AUTHORITY_ID.fullmatch(value):
                raise ValueError("invalid_session_authority_identifier")
        timestamp(self.created_at)

    @property
    def id(self) -> str:
        return self.identity.id

    def to_node_properties(self) -> dict[str, Any]:
        return {
            "kind": SESSION_REF_LABEL,
            "schema_version": 1,
            **asdict(self.identity),
            "ref_type": self.identity.ref_type,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "created_at": timestamp(self.created_at),
        }

    @classmethod
    def from_node_properties(cls, ref_id: str, properties: dict[str, Any]) -> SessionRef:
        properties = dict(properties)
        if properties.pop("kind", SESSION_REF_LABEL) != SESSION_REF_LABEL:
            raise ValueError("session_identity_conflict")
        expected = {
            "schema_version",
            "application",
            "source_instance_id",
            "external_session_id",
            "ref_type",
            "actor_id",
            "session_id",
            "correlation_id",
            "created_at",
        }
        if set(properties) != expected or type(properties["schema_version"]) is not int:
            raise ValueError("invalid_session_ref_shape")
        if properties["schema_version"] != 1:
            raise ValueError("unsupported_session_ref_version")
        identity = SessionIdentity(
            **{
                key: properties[key]
                for key in (
                    "application",
                    "source_instance_id",
                    "external_session_id",
                )
            }
        )
        if ref_id != identity.id or properties["ref_type"] != identity.ref_type:
            raise ValueError("session_identity_conflict")
        return cls(
            identity,
            properties["actor_id"],
            properties["session_id"],
            properties["correlation_id"],
            datetime.fromisoformat(properties["created_at"]),
        )

    @classmethod
    def from_legacy(cls, ref: HermesSessionRef, *, source_instance_id: str) -> SessionRef:
        """Explicit namespace required; legacy profile/provider cannot prove host identity."""
        return cls(
            SessionIdentity("hermes", source_instance_id, ref.external_session_id),
            ref.actor_id,
            ref.session_id,
            ref.correlation_id,
            ref.created_at,
        )


@dataclass(frozen=True)
class SessionLink:
    """Immutable witnessed relation; event ID is scoped by the observing source.

    Delegation: parent → child. Fork: new child → original parent.
    Repeated delegations/resumes carry distinct attempt IDs, not new session IDs.
    """

    source_instance_id: str
    event_id: str
    attempt_id: str
    relation: str
    origin: SessionIdentity
    target: SessionIdentity
    observed_at: datetime
    basis: str = "declared"

    def __post_init__(self) -> None:
        for value in (self.source_instance_id, self.event_id, self.attempt_id):
            opaque(value)
        if self.relation not in {"delegated_to", "forked_from", "uses_runtime"}:
            raise ValueError("invalid_session_relation")
        if self.basis not in {"declared", "source_observed"}:
            raise ValueError("invalid_session_link_basis")
        if not isinstance(self.origin, SessionIdentity) or not isinstance(
            self.target, SessionIdentity
        ):
            raise ValueError("invalid_session_link_identity")
        if self.origin == self.target:
            raise ValueError("self_session_link")
        timestamp(self.observed_at)

    @property
    def id(self) -> str:
        # Same source event cannot silently be rebound to different sessions.
        return digest_id("sl-", [self.source_instance_id, self.event_id])

    def to_node_properties(self) -> dict[str, Any]:
        return {
            "kind": SESSION_LINK_LABEL,
            "schema_version": 1,
            "source_instance_id": self.source_instance_id,
            "event_id": self.event_id,
            "attempt_id": self.attempt_id,
            "relation": self.relation,
            "origin_id": self.origin.id,
            "target_id": self.target.id,
            "observed_at": timestamp(self.observed_at),
            "basis": self.basis,
        }


def work_ref(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) != 2 or parts[0] not in {"Proposal", "Initiative", "Project", "Issue", "Task"}:
        raise ValueError("invalid_session_work_ref")
    validate_work_object_id(parts[1])
    return parts[0], parts[1]
