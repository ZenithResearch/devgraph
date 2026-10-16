"""Canonical product contract for Devgraph delegated read credentials.

This module owns the closed scope vocabulary and semantic validation shared by
request review, provisioning, registry loading, and the delegated receiver.
It contains no bearer handling and no Dregg implementation details.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCOPE_GRAPH_READ = "devgraph.graph.read"
SCOPE_WORK_READ = "devgraph.work.read"
SCOPE_ARENA_READ = "devgraph.arena.read"
SCOPE_OBSERVATION_READ = "devgraph.observation.read"
SCOPE_MATERIAL_READ = "devgraph.material.read"
SCOPE_DOCUMENT_READ = "devgraph.document.read"
SCOPE_QUERY_READ = "devgraph.query.read"

DELEGATED_SCOPE_ORDER = (
    SCOPE_GRAPH_READ,
    SCOPE_WORK_READ,
    SCOPE_ARENA_READ,
    SCOPE_OBSERVATION_READ,
    SCOPE_MATERIAL_READ,
    SCOPE_DOCUMENT_READ,
    SCOPE_QUERY_READ,
)
ALL_DELEGATED_SCOPES = frozenset(DELEGATED_SCOPE_ORDER)
WORK_OPERATION_ORDER = ("list", "get", "relationships", "children", "blockers")
RELATIONSHIP_TYPE_ORDER = (
    "children",
    "parent",
    "dependencies",
    "dependents",
    "blockers",
    "blocked",
)
WORK_KIND_ORDER = ("Proposal", "Initiative", "Project", "Issue", "Task")

DENIAL_REASONS = (
    "invalid_credential",
    "credential_not_yet_valid",
    "credential_expired",
    "credential_revoked",
    "credential_superseded",
    "scope_not_granted",
    "resource_not_granted",
    "invalid_request",
    "unsupported_profile",
    "unsupported_version",
    "request_binding_mismatch",
    "grant_amplification",
    "issuer_mismatch",
    "audience_mismatch",
    "origin_not_allowed",
    "version_rollback",
    "replacement_chain_invalid",
)

DelegatedScope: TypeAlias = Literal[
    "devgraph.graph.read",
    "devgraph.work.read",
    "devgraph.arena.read",
    "devgraph.observation.read",
    "devgraph.material.read",
    "devgraph.document.read",
    "devgraph.query.read",
]
WorkOperation: TypeAlias = Literal["list", "get", "relationships", "children", "blockers"]
RelationshipType: TypeAlias = Literal[
    "children", "parent", "dependencies", "dependents", "blockers", "blocked"
]
WorkKind: TypeAlias = Literal["Proposal", "Initiative", "Project", "Issue", "Task"]
SimpleScope: TypeAlias = Literal[
    "devgraph.graph.read",
    "devgraph.observation.read",
    "devgraph.material.read",
    "devgraph.document.read",
    "devgraph.query.read",
]


def _ordered_unique(value: Any, order: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field_name} must be a non-empty array")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must contain strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} contains duplicates")
    ranks = {item: index for index, item in enumerate(order)}
    if any(item not in ranks for item in value):
        raise ValueError(f"{field_name} contains an unknown value")
    return tuple(sorted(value, key=ranks.__getitem__))


def _selector(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field_name} must be a non-empty array when supplied")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field_name} must contain identifiers")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} contains duplicates")
    return tuple(sorted(value))


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkReadGrant(_StrictContract):
    scope: Literal["devgraph.work.read"]
    operations: tuple[WorkOperation, ...]
    relationship_types: tuple[RelationshipType, ...]
    work_kinds: tuple[WorkKind, ...]
    work_ids: tuple[str, ...] = ()
    arena_ids: tuple[str, ...] = ()
    include_archived: bool

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        for name, order in (
            ("operations", WORK_OPERATION_ORDER),
            ("relationship_types", RELATIONSHIP_TYPE_ORDER),
            ("work_kinds", WORK_KIND_ORDER),
        ):
            if name in result:
                result[name] = _ordered_unique(result[name], order, name)
        for name in ("work_ids", "arena_ids"):
            if name in result:
                result[name] = _selector(result[name], name)
        return result


class ArenaReadGrant(_StrictContract):
    scope: Literal["devgraph.arena.read"]
    arena_ids: tuple[str, ...]

    @field_validator("arena_ids", mode="before")
    @classmethod
    def normalize_arena_ids(cls, value: Any) -> tuple[str, ...]:
        return _selector(value, "arena_ids")


class SimpleReadGrant(_StrictContract):
    scope: SimpleScope


ReadGrant: TypeAlias = Annotated[
    WorkReadGrant | ArenaReadGrant | SimpleReadGrant,
    Field(discriminator="scope"),
]


def canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class _GrantSet(_StrictContract):
    @staticmethod
    def _validate_grants(scopes: tuple[str, ...], grants: tuple[ReadGrant, ...]) -> None:
        if frozenset(scopes) != frozenset(grant.scope for grant in grants):
            raise ValueError("scope and grant sets differ")
        fingerprints = [canonical_json(grant) for grant in grants]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("duplicate canonical grant")


class CredentialRequestProfile(_GrantSet):
    schema_: Literal["devgraph.credential-request-profile.v1"] = Field(alias="schema")
    issuer: str = Field(min_length=1, max_length=256)
    audience: Literal["devgraph"]
    resource: Literal["https://work.zenith-research.ca/devgraph"]
    requested_scopes: tuple[DelegatedScope, ...]
    requested_grants: tuple[ReadGrant, ...]
    reason: str = Field(min_length=1, max_length=1024)

    @field_validator("requested_scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        return _ordered_unique(value, DELEGATED_SCOPE_ORDER, "requested_scopes")

    @model_validator(mode="after")
    def validate_grants(self) -> CredentialRequestProfile:
        self._validate_grants(self.requested_scopes, self.requested_grants)
        return self


class CredentialManifest(_GrantSet):
    schema_: Literal["devgraph.credential-manifest.v1"] = Field(alias="schema")
    credential_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)
    subject_public_key: str = Field(min_length=1, max_length=4096)
    granted_scopes: tuple[DelegatedScope, ...]
    audience: Literal["devgraph"]
    resource: Literal["https://work.zenith-research.ca/devgraph"]
    allowed_origins: tuple[str, ...]
    read_grants: tuple[ReadGrant, ...]
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    replaces_credential_id: str | None
    issuer_key_id: str = Field(min_length=1, max_length=256)

    @field_validator("granted_scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        return _ordered_unique(value, DELEGATED_SCOPE_ORDER, "granted_scopes")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def validate_origins(cls, value: Any) -> tuple[str, ...]:
        origins = _selector(value, "allowed_origins")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("allowed_origins must contain HTTPS origins")
        return origins

    @model_validator(mode="after")
    def validate_manifest(self) -> CredentialManifest:
        self._validate_grants(self.granted_scopes, self.read_grants)
        if not self.issued_at.tzinfo or not self.not_before.tzinfo or not self.expires_at.tzinfo:
            raise ValueError("manifest timestamps must include an offset")
        if not self.issued_at <= self.not_before < self.expires_at:
            raise ValueError("invalid credential validity interval")
        if SCOPE_QUERY_READ in self.granted_scopes and not any(
            _is_unrestricted_query_prerequisite(grant)
            for grant in self.read_grants
            if isinstance(grant, WorkReadGrant)
        ):
            raise ValueError("query scope requires an unrestricted complete Work grant")
        return self


def _is_unrestricted_query_prerequisite(grant: WorkReadGrant) -> bool:
    return (
        grant.include_archived
        and grant.operations == WORK_OPERATION_ORDER
        and grant.relationship_types == RELATIONSHIP_TYPE_ORDER
        and grant.work_kinds == WORK_KIND_ORDER
        and not grant.work_ids
        and not grant.arena_ids
    )


def _selector_subset(granted: tuple[str, ...], requested: tuple[str, ...]) -> bool:
    if not requested:
        return True
    return bool(granted) and set(granted).issubset(requested)


def _grant_is_subset(granted: ReadGrant, requested: ReadGrant) -> bool:
    if granted.scope != requested.scope:
        return False
    if isinstance(granted, WorkReadGrant) and isinstance(requested, WorkReadGrant):
        return (
            set(granted.operations).issubset(requested.operations)
            and set(granted.relationship_types).issubset(requested.relationship_types)
            and set(granted.work_kinds).issubset(requested.work_kinds)
            and (not granted.include_archived or requested.include_archived)
            and _selector_subset(granted.work_ids, requested.work_ids)
            and _selector_subset(granted.arena_ids, requested.arena_ids)
        )
    if isinstance(granted, ArenaReadGrant) and isinstance(requested, ArenaReadGrant):
        return set(granted.arena_ids).issubset(requested.arena_ids)
    return type(granted) is type(requested)


def validate_manifest_subset(
    profile: CredentialRequestProfile,
    manifest: CredentialManifest,
) -> None:
    """Reject any manifest that widens or rebinds the approved request profile."""

    if manifest.issuer_key_id != profile.issuer:
        raise ValueError("issuer_mismatch")
    if manifest.audience != profile.audience:
        raise ValueError("audience_mismatch")
    if manifest.resource != profile.resource:
        raise ValueError("request_binding_mismatch")
    if not set(manifest.granted_scopes).issubset(profile.requested_scopes):
        raise ValueError("grant_amplification")
    for grant in manifest.read_grants:
        if not any(_grant_is_subset(grant, requested) for requested in profile.requested_grants):
            raise ValueError("grant_amplification")
