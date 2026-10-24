"""Compile Transfer's reviewed owner configuration; never activate authority here.

The installation boundary must independently recheck local resource/configuration
fingerprints, current native policy, scope paths, TLS and final owner approval.
A portable review file or Wallet public key is not an administrative credential.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator

from devgraph.auth.access_catalog import DEFAULT_PRESETS, PublishedPath
from devgraph.auth.access_policy import _Closed
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.grant_validity import GrantValidity
from devgraph.auth.standing_access import StandingAccessDraft, StandingOperatorPolicy
from devgraph.model.validation import validate_work_object_id
from devgraph.resource_paths import ResourcePath

OPERATIONS = next(p for p in DEFAULT_PRESETS if p.id == "project-manager").write_operations
SHORT_OPERATIONS = tuple(op.removeprefix("devgraph.work.").removesuffix(".v2") for op in OPERATIONS)


class PresetSeatAssociation(_Closed):
    seat_class_iri: str = Field(min_length=1, max_length=2048)
    seat_type_iri: str = Field(min_length=1, max_length=2048)
    label: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def valid(self):
        if self.seat_class_iri != "https://zenith-research.ca/ontology/terms/Seat":
            raise ValueError("use the shared Zenith Seat class")
        for raw in (self.seat_class_iri, self.seat_type_iri):
            uri = urlsplit(raw)
            if (
                any(c.isspace() or ord(c) < 32 for c in raw)
                or uri.username is not None
                or uri.password is not None
            ):
                raise ValueError("invalid ontology reference")
            if not (
                (uri.scheme == "https" and uri.hostname)
                or (raw.startswith("urn:") and len(raw) > 4)
            ):
                raise ValueError("ontology reference must be an absolute HTTPS or URN IRI")
        if not self.label.strip():
            raise ValueError("seat type label required")
        return self


class CredentialPresetRevision(_Closed):
    schema_: Literal["zenith.credential-preset.v1"] = Field(alias="schema")
    preset_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    revision: int = Field(strict=True, ge=1, le=9007199254740991)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=2000)
    read_operations: tuple[Literal["list", "get", "relationships"], ...] = Field(max_length=3)
    write_operations: tuple[str, ...] = Field(max_length=12)
    seat: PresetSeatAssociation | None = None
    archived: bool = Field(strict=True)

    @model_validator(mode="after")
    def valid(self):
        if "seat" in self.model_fields_set and self.seat is None:
            raise ValueError("omit an absent seat association")
        if not self.name.strip() or not (self.read_operations or self.write_operations):
            raise ValueError("preset needs name and permissions")
        if self.read_operations != tuple(
            op for op in ("list", "get", "relationships") if op in self.read_operations
        ):
            raise ValueError("invalid read operation order or duplicates")
        if self.write_operations != tuple(
            op for op in SHORT_OPERATIONS if op in self.write_operations
        ):
            raise ValueError("invalid write operation order, unknown operation or duplicates")
        return self


class OwnerContainer(_Closed):
    kind: Literal["Arena", "Initiative", "Project"]
    resource_id: str
    title: str = Field(min_length=1, max_length=4096)
    path: tuple[str, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def valid(self):
        validate_work_object_id(self.resource_id)
        expected = ("Arena", "Initiative", "Project")[
            : ("Arena", "Initiative", "Project").index(self.kind) + 1
        ]
        if len(self.path) != len(expected) or self.path[-1] != self.kind + "/" + self.resource_id:
            raise ValueError("invalid container path")
        for kind, item in zip(expected, self.path, strict=True):
            if not item.startswith(kind + "/"):
                raise ValueError("invalid container path")
            validate_work_object_id(item[len(kind) + 1 :])
        return self


class OwnerSetupPlan(_Closed):
    schema_: Literal[
        "zenith.devgraph-owner-setup.v1",
        "zenith.devgraph-owner-setup.v2",
        "zenith.devgraph-owner-setup.v3",
    ] = Field(alias="schema")
    status: Literal["prepared_not_applied"]
    resource_id: str
    resource_name: str = Field(min_length=1, max_length=80)
    local_endpoint: str
    registration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_public_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    wallet_endpoint: str = Field(min_length=1, max_length=2048)
    preset: Literal["reader", "contributor", "project-manager", "custom"]
    write_operations: tuple[str, ...] = Field(max_length=12)
    read_operations: tuple[Literal["list", "get", "relationships"], ...] | None = None
    preset_snapshot: CredentialPresetRevision | None = None
    scope_mode: Literal["global", "paths"]
    paths: tuple[OwnerContainer, ...] = Field(max_length=16)
    not_before: int = Field(strict=True, ge=0, le=9007199254740991)
    not_after: int | None = Field(default=None, strict=True, ge=0, le=9007199254740991)
    publish_hierarchy: bool = Field(strict=True)

    @model_validator(mode="after")
    def valid(self):
        if self.schema_ == "zenith.devgraph-owner-setup.v3":
            source = urlsplit(self.local_endpoint)
            if (
                source.scheme != "http"
                or source.hostname != "127.0.0.1"
                or source.path
                or source.query
                or source.fragment
                or source.username is not None
                or source.password is not None
                or source.port is None
                or not 1024 <= source.port <= 65535
                or self.local_endpoint != f"http://127.0.0.1:{source.port}"
                or urlsplit(self.wallet_endpoint).hostname != "127.0.0.1"
                or self.preset != "project-manager"
                or self.scope_mode != "global"
                or self.paths
                or self.not_after is not None
                or self.publish_hierarchy
                or self.write_operations != SHORT_OPERATIONS
                or self.read_operations != ("list", "get", "relationships")
            ):
                raise ValueError("local default-owner upgrade required")
        elif self.local_endpoint != "http://127.0.0.1:8080":
            raise ValueError("historical local endpoint changed")
        if self.schema_ == "zenith.devgraph-owner-setup.v1":
            if {"read_operations", "preset_snapshot"} & self.model_fields_set:
                raise ValueError("v1 setup cannot contain v2 fields")
        else:
            reads = self.read_operations
            if reads is None or reads != tuple(
                op for op in ("list", "get", "relationships") if op in reads
            ):
                raise ValueError("v2 setup requires ordered explicit reads")
            if not (reads or self.write_operations):
                raise ValueError("empty owner permission ceiling")
            if "preset_snapshot" in self.model_fields_set and self.preset_snapshot is None:
                raise ValueError("omit an absent preset snapshot")
            snapshot = self.preset_snapshot
            if snapshot is not None and (
                snapshot.archived
                or self.preset != "custom"
                or snapshot.read_operations != reads
                or snapshot.write_operations != self.write_operations
            ):
                raise ValueError("preset snapshot differs from explicit permissions")
        if str(UUID(self.resource_id)) != self.resource_id:
            raise ValueError("invalid resource identity")
        endpoint = urlsplit(self.wallet_endpoint)
        host = endpoint.hostname
        local = host in ("localhost", "127.0.0.1")
        if not local:
            try:
                ip = ipaddress.IPv4Address(host)
                local = any(
                    ip in ipaddress.ip_network(net)
                    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                )
            except (ValueError, TypeError):
                pass
        if not (
            local
            and endpoint.scheme == "https"
            and not endpoint.path
            and not endpoint.query
            and not endpoint.fragment
            and endpoint.username is None
            and endpoint.password is None
            and endpoint.port
            and 1024 <= endpoint.port <= 65535
            and endpoint.port not in (8080, 7687)
        ):
            raise ValueError("local HTTPS Wallet address required")
        expected = dict(
            reader=(), contributor=SHORT_OPERATIONS[:7], **{"project-manager": SHORT_OPERATIONS}
        )
        if (
            len(set(self.write_operations)) != len(self.write_operations)
            or set(self.write_operations) - set(SHORT_OPERATIONS)
            or self.preset != "custom"
            and self.write_operations != expected[self.preset]
        ):
            raise ValueError("permission selection differs from preset")
        if (self.scope_mode == "global") != (not self.paths):
            raise ValueError("select explicit global scope or nonempty paths")
        keys = [p.kind + "/" + p.resource_id for p in self.paths]
        if keys != sorted(set(keys)) or any(
            a != b and b.path[: len(a.path)] == a.path for a in self.paths for b in self.paths
        ):
            raise ValueError("duplicate or redundant scope paths")
        GrantValidity(self.not_before, self.not_after)
        return self


def decode_plan(raw: bytes) -> OwnerSetupPlan:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError("invalid owner setup size")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate setup field")
            value[key] = item
        return value

    return OwnerSetupPlan.model_validate(json.loads(raw, object_pairs_hook=unique))


def compile_operator_policy(
    plan: OwnerSetupPlan, *, now: int, published_global_roots: tuple[ResourcePath, ...] = ()
) -> StandingOperatorPolicy:
    """Return proposed policy only. No IO, signing, grants, keys or local activation."""
    validity = GrantValidity(plan.not_before, plan.not_after)
    if not validity.contains(now):
        raise ValueError("owner setup validity is not current")
    paths = [
        ResourcePath.model_validate({"schema": "devgraph.resource-path.v1", "path": item.path})
        for item in plan.paths
    ]
    published = paths if plan.scope_mode == "paths" else list(published_global_roots)
    if plan.publish_hierarchy and (not published or len(published) > 16):
        raise ValueError("publishing needs the reviewed current scope inventory")
    if plan.scope_mode == "global" and any(
        len(p.path) != 1 or not p.path[0].startswith("Arena/") for p in published
    ):
        raise ValueError("global publication needs current Arena roots")
    # Policy identity derives from stable local resource ID, never a test fixture.
    issuer = "devgraph-" + plan.resource_id
    read = dict(
        scope="devgraph.work.read",
        operations=plan.read_operations
        if plan.read_operations is not None
        else ("list", "get", "relationships"),
        work_kinds=("Initiative", "Project", "Issue", "Task"),
        include_archived=False,
        relationship_types=(
            "children",
            "parent",
            "dependencies",
            "dependents",
            "blockers",
            "blocked",
        ),
    )
    resources = [dict(kind=kind) for kind in read["work_kinds"]]
    writes = [
        dict(operation="devgraph.work." + op + ".v2", resources=resources)
        for op in plan.write_operations
    ]
    if paths:
        read.update(schema="devgraph.path-read-grant.v1", resource_paths=paths)
        for item in writes:
            item.update(schema="devgraph.path-write-grant.v1", resource_paths=paths)
    ceiling = StandingAccessDraft.model_validate(
        dict(
            schema="devgraph.access-draft.v5",
            issuer=issuer,
            receiver=plan.wallet_endpoint,
            validity=json.loads(validity.to_json()),
            lifetime_seconds=900,
            read_grants=[read] if read["operations"] else [],
            write_grants=writes,
            write_audience="devgraph://receiver-local",
        )
    )
    return StandingOperatorPolicy.model_validate(
        dict(
            schema="devgraph.access-operator-policy.v4",
            revision=1,
            operator_public_keys=[plan.owner_public_key],
            ceiling=ceiling,
            published_paths=[
                PublishedPath(resource_path=p, discover_descendants=True) for p in published
            ]
            if plan.publish_hierarchy
            else [],
        )
    )


def plan_digest(plan: OwnerSetupPlan) -> str:
    return hashlib.sha256(
        canonical_json(plan.model_dump(mode="json", by_alias=True, exclude_none=True))
    ).hexdigest()
