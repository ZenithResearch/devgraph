"""Receiver-owned resource restrictions, additional to verified secS authority.

This is not a credential, native policy, or delegation format. Historical access
drafts remain closed: an Arena predicate must never become a native prefix rule.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator, model_validator

from devgraph.arenas import ArenaConflict, ArenaRepository
from devgraph.auth.access_policy import WriteGrant, _Closed, _draft_validity
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.secs_work import SecSWorkAdapter, SecSWorkDenied
from devgraph.events.outbox import _require_matching_scope, digest_idempotency_claim
from devgraph.model.repository import MissingWorkObjectError
from devgraph.model.validation import validate_work_object_id
from devgraph.progress import TODO_KINDS
from devgraph.resource_paths import paths_subset, within_paths


class ScopedWriteGrant(WriteGrant):
    # None is unrestricted; an empty selector never grants anything.
    arena_ids: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("arena_ids", mode="before")
    @classmethod
    def arenas(cls, value):
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("Arena selection must be a nonempty array")
        for identifier in value:
            validate_work_object_id(identifier)
        if len(set(value)) != len(value):
            raise ValueError("duplicate Arena")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def eligible(self):
        if self.arena_ids is not None and any(
            r.kind not in (*TODO_KINDS, "Arena") for r in self.resources
        ):
            raise ValueError("provenance records need separate explicit resource grants")
        return self


class WriteScopeAssignment(_Closed):
    actor_id: str = Field(pattern=r"^pubkey:sha256:[0-9a-f]{64}$")
    not_before: int = Field(strict=True, ge=0, le=9007199254740991)
    not_after: int = Field(strict=True, ge=1, le=9007199254740991)
    grants: tuple[ScopedWriteGrant, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def bounded(self):
        if self.not_before >= self.not_after:
            raise ValueError("invalid grant interval")
        if len({canonical_json(g) for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate grant")
        return self


class ReceiverWritePolicy(_Closed):
    schema_: Literal["devgraph.receiver-write-policy.v1"] = Field(alias="schema")
    revision: int = Field(strict=True, ge=1, le=9007199254740991)
    stable_issuer: str = Field(min_length=1, max_length=256)
    audience: Literal["devgraph://receiver-local"]
    native_policy_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    native_policy_version: int = Field(strict=True, ge=1, le=9007199254740991)
    native_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignments: tuple[WriteScopeAssignment, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def unique(self):
        if self.schema_ == "devgraph.receiver-write-policy.v1" and any(
            getattr(g, "resource_paths", ()) for a in self.assignments for g in a.grants
        ):
            raise ValueError("resource paths require receiver write policy v2")
        if len({g.actor_id for g in self.assignments}) != len(self.assignments):
            raise ValueError("duplicate actor assignment")
        if len(canonical_json(self)) > 262144:
            raise ValueError("write scope policy too large")
        return self

    @property
    def digest(self):
        return hashlib.sha256(canonical_json(self)).hexdigest()

    def authorize(self, verified, *, now):
        native = verified.safe_summary
        if (
            type(now) is not int
            or now < 0
            or verified.principal.issuer != self.stable_issuer
            or verified.principal.audience != self.audience
            or native["receiver_policy_id"] != self.native_policy_id
            or native["receiver_policy_version"] != self.native_policy_version
            or native["receiver_policy_digest_sha256"] != self.native_policy_digest_sha256
        ):
            raise SecSWorkDenied("write_scope_binding_changed")
        assignment = next(
            (a for a in self.assignments if a.actor_id == verified.principal.actor_id), None
        )
        if assignment is None or not _draft_validity(assignment).contains(now):
            raise SecSWorkDenied("write_scope_not_current")
        return ResourceScopeGuard(self, assignment, verified.request.authority_operation)


def require_write_scope_subset(child: WriteScopeAssignment, parent: WriteScopeAssignment):
    """Narrow one issuer-owned assignment without crossing operation/selector pairs."""
    if child.actor_id != parent.actor_id or not _draft_validity(child).contained_by(
        _draft_validity(parent)
    ):
        raise ValueError("write assignment amplification")
    for grant in child.grants:
        parents = [
            p
            for p in parent.grants
            if p.operation == grant.operation
            and paths_subset(getattr(grant, "resource_paths", ()), getattr(p, "resource_paths", ()))
            and (
                p.arena_ids is None
                or grant.arena_ids is not None
                and set(grant.arena_ids) <= set(p.arena_ids)
            )
        ]
        if any(
            not any(r.contained_by(allowed) for p in parents for allowed in p.resources)
            for r in grant.resources
        ):
            raise ValueError("write resource or Arena amplification")


@dataclass(frozen=True)
class ResourceScopeGuard:
    policy: ReceiverWritePolicy
    assignment: WriteScopeAssignment
    operation: str

    @property
    def summary(self):
        return dict(
            write_scope_policy_revision=self.policy.revision,
            write_scope_policy_digest_sha256=self.policy.digest,
        )

    def prepare(self, storage, request):
        witnesses, cache = [], {}
        for resource in request.resources:
            kind, identifier = resource.split("/", 1)
            candidates = tuple(
                grant
                for grant in self.assignment.grants
                if grant.operation == self.operation
                and any(
                    r.kind == kind and (r.ids is None or identifier in r.ids)
                    for r in grant.resources
                )
                and _inside(storage, kind, identifier, grant, cache)
            )
            if not candidates:
                raise SecSWorkDenied("write_resource_not_granted")
            witnesses.append((kind, identifier, candidates))
        return ScopedMutation(tuple(witnesses))


def _inside(storage, kind, identifier, grant, cache):
    if not within_paths(storage, kind, identifier, getattr(grant, "resource_paths", ())):
        return False
    if grant.arena_ids is None:
        return True
    key = kind, identifier
    if key not in cache:
        arena = None
        try:
            repository = ArenaRepository(storage)
            if kind == "Arena":
                arena = repository.get(identifier).id
            elif kind in TODO_KINDS:
                membership = repository.membership(kind, identifier)
                arena = membership.arena.id if membership.arena else None
        except (ArenaConflict, MissingWorkObjectError, ValueError):
            pass  # A missing/ambiguous scope is never an unrestricted resource.
        cache[key] = arena
    return cache[key] is not None and cache[key] in grant.arena_ids


@dataclass(frozen=True)
class ScopedMutation:
    witnesses: tuple

    def check_after(self, storage):
        cache = {}
        for kind, identifier, candidates in self.witnesses:
            # The same grant must cover both states; independent grants cannot
            # launder a move from Arena A into Arena B.
            if not any(_inside(storage, kind, identifier, grant, cache) for grant in candidates):
                raise SecSWorkDenied("write_resource_scope_changed")


class ScopedSecSWorkAdapter(SecSWorkAdapter):
    """Explicit composition only. Missing policy never falls back to broad writes.

    The trusted loader must provide current receiver-owned policy. Activation,
    persistence and anti-rollback belong to the owner registry, not this parser.
    No caller-supplied policy or credential can select this loader.
    """

    def __init__(self, *, policy, clock, compound_host=None, **kwargs):
        if not callable(policy) or not callable(clock):
            raise TypeError("current policy and clock required")
        super().__init__(**kwargs)
        self.policy, self.clock, self.compound_host = policy, clock, compound_host

    def _resource_scope(self, verified):
        try:
            policy = self.policy()
            if not isinstance(policy, ReceiverWritePolicy):
                raise ValueError()
            guard = policy.authorize(verified, now=self.clock())
            if self.compound_host is not None:
                from devgraph.auth.paired_resources import PairedGuard

                return PairedGuard(guard, self.compound_host)
            return guard
        except (ValueError, TypeError, KeyError, OSError):
            raise SecSWorkDenied("write_scope_unavailable") from None

    def status(self, *, request_json, projection_json, idempotency_key):
        """Current scope plus a fresh proof; never dispatch or repeat a mutation."""
        with self.audit_log.transaction():
            with self.storage.work_mutation_transaction():
                verified = self.verifier.verify(
                    request_json=request_json,
                    projection_json=projection_json,
                    idempotency_key=idempotency_key,
                )
                request = verified.request
                scope = self._resource_scope(verified)
                scope.prepare(self.storage, request)
                claim = digest_idempotency_claim(verified.principal, idempotency_key)
                receipt = self.outbox._find_receipt_by_idempotency_digest(
                    "idempotency_claim_digest", claim
                )
                if receipt is not None:
                    kind, identifier = (
                        ("Issue", request.payload["issue_id"])
                        if request.operation == "convert"
                        else (request.kind, request.id)
                    )
                    _require_matching_scope(
                        receipt,
                        operation=request.authority_operation,
                        subject_label=kind,
                        subject_id=identifier,
                        request_digest_sha256=request.digest,
                    )
                self.audit_log.record(
                    actor_id=verified.principal.actor_id,
                    session_id=verified.principal.session_id,
                    correlation_id=verified.principal.correlation_id,
                    category="read",
                    operation=request.authority_operation,
                    safe_summary={
                        **verified.safe_summary,
                        **scope.summary,
                        "status_only": True,
                        "receipt_id": receipt.id if receipt else None,
                        "outcome": "committed" if receipt else "unknown",
                    },
                )
                return receipt
