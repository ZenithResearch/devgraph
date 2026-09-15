from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import Any, Literal, NamedTuple, Protocol

from devgraph.storage.supporting_material import SupportingReference


@dataclass(frozen=True)
class NodeRecord:
    label: str
    id: str
    properties: dict[str, Any] = field(default_factory=dict)
    archived: bool = False


@dataclass(frozen=True)
class EdgeRecord:
    from_label: str
    from_id: str
    relationship: str
    to_label: str
    to_id: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkContainment:
    """One Work record with up to two incoming edges of each containment type.

    A second edge is an ambiguity witness, not a truncated valid result.
    Keep duplicate relationships so callers can reject ambiguous parentage.
    """

    node: NodeRecord
    parents: tuple[EdgeRecord, ...]
    memberships: tuple[EdgeRecord, ...]


@dataclass(frozen=True)
class ConstraintResult:
    applied_count: int
    existing_count: int


@dataclass(frozen=True)
class HealthStatus:
    live: bool
    ready: bool
    detail: str


@dataclass(frozen=True)
class EventReceiptClaim:
    """Result of atomically claiming one principal-scoped retry identity."""

    created: bool
    receipt: NodeRecord


class StorageUnavailable(RuntimeError):
    """Raised when canonical storage cannot be reached or used."""


class BootstrapConstraint(NamedTuple):
    name: str
    constraint_type: str
    label: str
    properties: tuple[str, ...]
    definition: str


class SchemaObject(NamedTuple):
    object_type: str
    definition: str


@dataclass(frozen=True)
class MigrationOwner:
    owner_attempt_id: str | None
    state: str
    runner_schema_version: int


@dataclass(frozen=True)
class MigrationJournal:
    version: int
    name: str
    checksum: str
    schema_object_name: str
    schema_object_type: str
    schema_object_definition: str
    state: str
    owner_attempt_id: str
    started_at: str
    completed_at: str | None = None
    runner_schema_version: int = 1

    @classmethod
    def pending(cls, migration: Any, owner: str, started_at: str | None = None) -> MigrationJournal:
        # Migration is ops-owned; structural access keeps storage independent of ops.
        from datetime import datetime, timezone

        timestamp = started_at or datetime.now(timezone.utc).isoformat()
        payload = migration.payload.decode("utf-8")
        normalized = " ".join(payload.rstrip(";\n").replace("`", "").split())
        import re

        match = re.fullmatch(
            r"CREATE CONSTRAINT (\S+)(?: IF NOT EXISTS)? FOR \(\w+:(\w+)\) "
            r"REQUIRE \(?\w+\.(\w+)\)? IS UNIQUE",
            normalized,
        )
        if match is not None:
            name, label, property_name = match.groups()
            normalized = (
                f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (m:{label}) "
                f"REQUIRE m.{property_name} IS UNIQUE"
            )
        return cls(
            migration.version,
            migration.name,
            migration.checksum,
            migration.name,
            "UNIQUENESS",
            normalized,
            "pending",
            owner,
            timestamp,
        )

    @classmethod
    def transactional_applied(
        cls, migration: Any, owner: str, completed_at: str
    ) -> MigrationJournal:
        return cls(
            migration.version,
            migration.name,
            migration.checksum,
            migration.name,
            "TRANSACTIONAL_DATA",
            f"transactional_data:{migration.name}:{migration.checksum}",
            "applied",
            owner,
            completed_at,
            completed_at,
        )

    @classmethod
    def started(cls, migration: Any, owner: str) -> MigrationJournal:
        return replace(cls.pending(migration, owner), state="ddl_started")


class MigrationStore(Protocol):
    """Narrow typed database boundary used by the migration journal."""

    def inspect_bootstrap_constraint(self) -> BootstrapConstraint | None: ...
    def execute_bootstrap(self, statement: str) -> None: ...
    def inspect_owner(self) -> MigrationOwner | None: ...
    def acquire_owner(self, attempt_id: str) -> bool: ...
    def recover_owner(
        self,
        expected_owner_attempt_id: str,
        new_attempt_id: str,
        journal: MigrationJournal | None,
    ) -> bool: ...
    def release_owner(self, attempt_id: str) -> bool: ...
    def inspect_journal(self) -> tuple[MigrationJournal, ...]: ...
    def compare_and_set_journal(
        self,
        before: MigrationJournal | None,
        after: MigrationJournal,
        attempt_id: str,
    ) -> bool: ...
    def execute_ddl(self, payload: bytes) -> None: ...
    def apply_transactional_data(
        self, migration: Any, attempt_id: str, completed_at: str
    ) -> bool: ...
    def inspect_schema_object(self, name: str) -> SchemaObject | Literal[False] | None: ...


class GraphStorage(Protocol):
    def create_node(
        self, label: str, node_id: str, properties: dict[str, Any] | None = None
    ) -> NodeRecord: ...

    def get_node(self, label: str, node_id: str) -> NodeRecord | None: ...

    def work_containment(self, label: str, node_id: str) -> WorkContainment | None: ...

    def arena_member_page(
        self, arena_id: str, *, after_resource: str | None = None, limit: int = 50
    ) -> list[WorkContainment]: ...

    def update_node(self, label: str, node_id: str, properties: dict[str, Any]) -> NodeRecord: ...

    def archive_node(
        self,
        label: str,
        node_id: str,
        properties: dict[str, Any] | None = None,
    ) -> NodeRecord: ...

    def create_edge(
        self,
        from_label: str,
        from_id: str,
        relationship: str,
        to_label: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> EdgeRecord: ...

    def related_work_nodes(
        self,
        label: str,
        node_id: str,
        relationship: str,
        *,
        incoming: bool,
        after_resource: str | None = None,
        limit: int = 50,
    ) -> list[NodeRecord]: ...

    def supporting_material_references(
        self,
        label: str,
        node_id: str,
        *,
        artifact_ids: tuple[str, ...],
        external_link_ids: tuple[str, ...],
        after_resource: str | None = None,
        target_resource: str | None = None,
        limit: int = 50,
    ) -> list[SupportingReference]: ...


    def supporting_material_nodes(self, keys: list[tuple[str, str]]) -> list[NodeRecord]: ...

    def list_edges(
        self, relationship: str | None = None, *, limit: int | None = None
    ) -> list[EdgeRecord]: ...

    def delete_edge(
        self, from_label: str, from_id: str, relationship: str, to_label: str, to_id: str
    ) -> None: ...

    def work_mutation_transaction(self) -> AbstractContextManager[None]: ...

    def query(
        self,
        label: str | None = None,
        archived: bool | None = None,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int | None = None,
    ) -> list[NodeRecord]: ...

    def inspect_canonical_persistence(self) -> None: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def health(self) -> HealthStatus: ...


class EventReceiptClaimStorage(Protocol):
    """Atomic receipt-claim capability required only by the event outbox."""

    def claim_event_receipt(
        self,
        node_id: str,
        idempotency_claim_digest: str,
        properties: dict[str, Any],
    ) -> EventReceiptClaim: ...


class EventOutboxStorage(GraphStorage, EventReceiptClaimStorage, Protocol):
    """Graph storage with the atomic claim primitive needed by EventOutbox."""
