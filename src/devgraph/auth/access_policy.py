"""Issuer-owned grant drafts. These are not credentials or child delegations.

Only final, operator-approved projections enter existing secS factories. Every
step is checked against its immediate parent; combining independent grants must
never create a Cartesian product of operations and resources.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devgraph.auth.delegated_contract import WorkReadGrant, _grant_is_subset, canonical_json
from devgraph.auth.native_read import EnrollmentPolicy
from devgraph.model.validation import validate_work_object_id
from devgraph.work_requests import WORK_OPERATIONS

WRITE_OPERATIONS = frozenset(
    f"devgraph.work.{op}.v2"
    for op in (
        *[op for op in WORK_OPERATIONS if op != "status"],
        "progress.set",
        "restore",
        "proposal.reject",
    )
) | frozenset(f"devgraph.arena.{op}.v1" for op in ("create", "patch", "archive", "member.set"))
ResourceKind = Literal[
    "Todo",
    "Proposal",
    "Initiative",
    "Project",
    "Issue",
    "Task",
    "Arena",
    "Decision",
    "ReviewPacket",
    "Handoff",
    "ExternalLink",
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WriteResource(_Closed):
    kind: ResourceKind
    # Omitted means the whole kind; [] never means unrestricted.
    ids: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("ids", mode="before")
    @classmethod
    def identifiers(cls, value):
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("ids must be a nonempty array when supplied")
        for identifier in value:
            validate_work_object_id(identifier)
        if len(set(value)) != len(value):
            raise ValueError("duplicate resource")
        return tuple(sorted(value))

    def contained_by(self, parent: WriteResource) -> bool:
        return self.kind == parent.kind and (
            parent.ids is None or self.ids is not None and set(self.ids) <= set(parent.ids)
        )


class WriteGrant(_Closed):
    operation: str
    resources: tuple[WriteResource, ...] = Field(min_length=1, max_length=64)

    @field_validator("operation")
    @classmethod
    def supported(cls, value):
        if value not in WRITE_OPERATIONS:
            raise ValueError("unsupported write operation")
        return value

    @model_validator(mode="after")
    def duplicates(self):
        if len({canonical_json(r) for r in self.resources}) != len(self.resources):
            raise ValueError("duplicate write resource")
        return self


class AccessDraft(_Closed):
    schema_: Literal["devgraph.access-draft.v1"] = Field(alias="schema")
    issuer: str
    receiver: str
    write_audience: Literal["devgraph://receiver-local"]
    subject_public_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    installation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    not_before: int = Field(strict=True, ge=0, le=9007199254740991)
    not_after: int = Field(strict=True, ge=1, le=9007199254740991)
    lifetime_seconds: int = Field(strict=True, ge=60, le=3600)
    read_grants: tuple[WorkReadGrant, ...] = Field(default=(), max_length=16)
    write_grants: tuple[WriteGrant, ...] = Field(default=(), max_length=64)
    previous_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        if self.not_before >= self.not_after or not (self.read_grants or self.write_grants):
            raise ValueError("a nonempty grant and valid interval are required")
        if self.installation_id is not None and self.subject_public_key is None:
            raise ValueError("installation binding requires a member")
        # Validate issuer/origin and the supported read vocabulary even for a
        # write-only draft. Do not silently extend the historical read decoder.
        placeholder = WorkReadGrant(
            scope="devgraph.work.read",
            operations=("get",),
            relationship_types=("parent",),
            work_kinds=("Task",),
            include_archived=False,
        )
        EnrollmentPolicy.model_validate(
            {
                "schema": "devgraph.native-read-policy.v1",
                "issuer": self.issuer,
                "receiver": self.receiver,
                "grants": self.read_grants or (placeholder,),
                "lifetime_seconds": self.lifetime_seconds,
            }
        )
        for grants in (self.read_grants, self.write_grants):
            if len({canonical_json(g) for g in grants}) != len(grants):
                raise ValueError("duplicate grant")
        return self

    @property
    def digest(self):
        return hashlib.sha256(canonical_json(self)).hexdigest()


def require_subset(child: AccessDraft, parent: AccessDraft) -> None:
    """Semantic ceiling check, reusable without asserting cryptographic delegation."""
    for field in ("issuer", "receiver", "write_audience"):
        if getattr(child, field) != getattr(parent, field):
            raise ValueError("issuer or receiver binding changed")
    for field in ("subject_public_key", "installation_id"):
        expected = getattr(parent, field)
        if expected is not None and getattr(child, field) != expected:
            raise ValueError("member binding changed")
    if (
        child.not_before < parent.not_before
        or child.not_after > parent.not_after
        or child.lifetime_seconds > parent.lifetime_seconds
    ):
        raise ValueError("validity amplification")
    if any(
        not any(_grant_is_subset(g, allowed) for allowed in parent.read_grants)
        for g in child.read_grants
    ):
        raise ValueError("read grant amplification")
    for grant in child.write_grants:
        # Each resource must be covered for this exact operation. We may union
        # resource sets, but never borrow a different operation's resources.
        allowed = [
            r for g in parent.write_grants if g.operation == grant.operation for r in g.resources
        ]
        if any(not any(r.contained_by(p) for p in allowed) for r in grant.resources):
            raise ValueError("write grant amplification")


def attenuate(parent: AccessDraft, child: AccessDraft) -> AccessDraft:
    if child.previous_digest != parent.digest:
        raise ValueError("previous draft digest mismatch")
    require_subset(child, parent)
    return child


def compile_read(draft: AccessDraft) -> EnrollmentPolicy:
    if draft.subject_public_key is None or draft.installation_id is None:
        raise ValueError("read issuance needs an exact member and installation")
    if not draft.read_grants:
        raise ValueError("no read grant selected")
    return EnrollmentPolicy.model_validate(
        {
            "schema": "devgraph.native-read-policy.v1",
            "issuer": draft.issuer,
            "receiver": draft.receiver,
            "grants": draft.read_grants,
            "lifetime_seconds": draft.lifetime_seconds,
        }
    )


def compile_write(draft: AccessDraft, *, policy_id: str, policy_version: int) -> dict:
    if any(getattr(grant, "arena_ids", None) is not None for grant in draft.write_grants):
        raise ValueError("Arena restrictions require the scoped receiver; cannot flatten")
    if draft.subject_public_key is None:
        raise ValueError("write issuance needs an exact member")
    if draft.installation_id is not None:
        # Existing named-write credentials bind the holder/caller, not a mobile
        # installation. Never discard an unsupported restriction on export.
        raise ValueError("installation-restricted write grants are not supported")
    if (
        not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", policy_id)
        or type(policy_version) is not int
        or not 1 <= policy_version <= 9007199254740991
    ):
        raise ValueError("invalid policy identity or version")
    if not draft.write_grants:
        raise ValueError("no write grant selected")
    actor = "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(draft.subject_public_key)).hexdigest()
    rules = []
    for grant in draft.write_grants:
        for resource in grant.resources:
            for identifier in resource.ids or ("",):
                rules.append(
                    {
                        "actor_id": actor,
                        "effect": "allow",
                        "status": "active",
                        "operation": grant.operation,
                        "resource": resource.kind + "/" + identifier,
                        "resource_match": "exact" if resource.ids else "prefix",
                        "not_before": draft.not_before,
                        "not_after": draft.not_after,
                    }
                )
    # Overlapping grants may project the same rule; do not duplicate it.
    unique = {canonical_json(r): r for r in rules}
    if len(unique) > 512:
        raise ValueError("too many write rules")
    return {
        "schema": "secs-devgraph-work-policy.v1",
        "schema_version": 1,
        "audience": draft.write_audience,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "rules": [unique[k] for k in sorted(unique)],
    }


class MemberAccessPolicy(_Closed):
    """Owner-private approved assignments, never supplied by an enrollment client.

    Removing an assignment denies both issuance and existing reads immediately.
    A narrowed assignment changes the signed challenge policy digest. Its absolute
    deadline also remains a live check, independent of credential renewal.
    """

    schema_: Literal["devgraph.member-access-policy.v1"] = Field(alias="schema")
    revision: int = Field(default=1, strict=True, ge=1, le=9007199254740991)
    ceiling: AccessDraft
    assignments: tuple[AccessDraft, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def bounded(self):
        seen = set()
        for grant in self.assignments:
            if grant.subject_public_key is None or grant.installation_id is None:
                raise ValueError("read assignment needs an exact member and installation")
            key = grant.subject_public_key, grant.installation_id
            if key in seen:
                raise ValueError("duplicate member installation")
            seen.add(key)
            require_subset(grant, self.ceiling)
        return self

    def assignment(self, owner: str, device: str, current: int) -> AccessDraft:
        for grant in self.assignments:
            if (
                grant.subject_public_key == owner
                and grant.installation_id == device
                and grant.not_before <= current < grant.not_after
                and grant.read_grants
            ):
                return grant
        raise ValueError("member access not granted")
