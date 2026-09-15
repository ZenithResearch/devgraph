from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from devgraph.ops.migrate import (
    BOOTSTRAP_CONSTRAINT_NAME,
    BOOTSTRAP_DDL,
    Manifest,
    MigrationJournal,
    MigrationStatus,
    apply_migrations,
    load_manifest,
)
from devgraph.storage.base import (
    BootstrapConstraint,
    MigrationOwner,
    SchemaObject,
)

ROOT = Path(__file__).parents[2]


class MemoryMigrationStore:
    def __init__(self) -> None:
        self.bootstrap = None
        self.owner = None
        self.journal: dict[int, MigrationJournal] = {}
        self.objects: dict[str, SchemaObject] = {}
        self.ddl_calls: list[bytes] = []
        self.transactional_calls: list[str] = []
        self.mutations = 0
        self.fail_ddl = False
        self.fail_transactional = False
        self.fail_transition = None
        self.inspection_unknown = False
        self.transitions: list[tuple[str | None, str]] = []

    def inspect_bootstrap_constraint(self):
        return self.bootstrap

    def execute_bootstrap(self, statement: str) -> None:
        assert statement == BOOTSTRAP_DDL
        self.bootstrap = BootstrapConstraint(
            BOOTSTRAP_CONSTRAINT_NAME,
            "UNIQUENESS",
            "DevgraphMigration",
            ("version",),
            BOOTSTRAP_DDL,
        )

    def inspect_owner(self):
        if self.owner is None and self.bootstrap is None:
            return None
        return MigrationOwner(self.owner, "owned" if self.owner is not None else "clean", 1)

    def acquire_owner(self, attempt_id: str) -> bool:
        if self.owner == attempt_id:
            return True
        if self.owner is not None:
            return False
        self.owner = attempt_id
        self.mutations += 1
        return True

    def recover_owner(
        self,
        expected_owner_attempt_id: str,
        new_attempt_id: str,
        journal: MigrationJournal | None,
    ) -> bool:
        if self.owner != expected_owner_attempt_id:
            return False
        if journal is None:
            if any(
                record.state not in {"applied", "recoverable_ddl_not_applied"}
                for record in self.journal.values()
            ):
                return False
            self.owner = new_attempt_id
            self.mutations += 1
            return True
        if self.journal.get(journal.version) != journal:
            return False
        self.owner = new_attempt_id
        self.journal[journal.version] = replace(journal, owner_attempt_id=new_attempt_id)
        self.mutations += 1
        return True

    def release_owner(self, attempt_id: str) -> bool:
        if self.owner != attempt_id:
            return False
        if any(
            record.state not in {"applied", "recoverable_ddl_not_applied"}
            for record in self.journal.values()
        ):
            return False
        self.owner = None
        self.mutations += 1
        return True

    def inspect_journal(self):
        return tuple(self.journal[key] for key in sorted(self.journal))

    def compare_and_set_journal(self, before, after, attempt_id: str) -> bool:
        if self.owner != attempt_id or self.fail_transition == after.state:
            return False
        current = self.journal.get(after.version)
        if current != before:
            return False
        self.journal[after.version] = after
        self.transitions.append((None if before is None else before.state, after.state))
        self.mutations += 1
        return True

    def execute_ddl(self, payload: bytes) -> None:
        self.ddl_calls.append(payload)
        if self.fail_ddl:
            raise RuntimeError("driver detail must be redacted")
        name = payload.decode().split()[2]
        self.objects[name] = SchemaObject(
            "UNIQUENESS", " ".join(payload.decode().rstrip(";\n").split())
        )

    def apply_transactional_data(self, migration, attempt_id: str, completed_at: str) -> bool:
        if self.fail_transactional:
            raise RuntimeError("transactional driver detail must be redacted")
        if self.owner != attempt_id or migration.name != "canonical_work_object_persistence_v1":
            return False
        record = MigrationJournal.transactional_applied(migration, attempt_id, completed_at)
        if migration.version in self.journal:
            return self.journal[migration.version] == record
        self.journal[migration.version] = record
        self.transactional_calls.append(migration.name)
        self.mutations += 1
        return True

    def inspect_schema_object(
        self, name: str
    ) -> SchemaObject | Literal[False] | None:
        if self.inspection_unknown:
            return None
        return self.objects.get(name, False)


def manifest() -> Manifest:
    return load_manifest(ROOT / "migrations/manifest.json")


def test_empty_first_run_applies_all_and_second_run_is_noop() -> None:
    store = MemoryMigrationStore()
    first = apply_migrations(manifest(), store, attempt_id="attempt-a")
    assert first == MigrationStatus.clean(26, manifest())
    assert len(store.ddl_calls) == 25
    assert store.transactional_calls == ["canonical_work_object_persistence_v1"]
    assert store.transitions[:4] == [
        (None, "pending"),
        ("pending", "ddl_started"),
        ("ddl_started", "ddl_observed"),
        ("ddl_observed", "applied"),
    ]
    second = apply_migrations(manifest(), store, attempt_id="attempt-b")
    assert second == first
    assert len(store.ddl_calls) == 25
    assert store.transactional_calls == ["canonical_work_object_persistence_v1"]


def test_bootstrap_is_infrastructure_not_application_version() -> None:
    store = MemoryMigrationStore()
    apply_migrations(manifest(), store, attempt_id="attempt-a")
    assert 0 not in store.journal
    assert min(store.journal) == 1


def test_ddl_failure_with_proven_absence_is_recoverable_without_loop() -> None:
    store = MemoryMigrationStore()
    store.fail_ddl = True
    result = apply_migrations(manifest(), store, attempt_id="attempt-a")
    assert result.reason == "recoverable_ddl_not_applied"
    assert store.journal[1].state == "recoverable_ddl_not_applied"
    assert len(store.ddl_calls) == 1
    store.fail_ddl = False
    result = apply_migrations(manifest(), store, attempt_id="attempt-b")
    assert result.ready is True


def test_same_owner_resumes_after_ddl_started_and_reconciles_exact_object() -> None:
    store = MemoryMigrationStore()
    migration = manifest().migrations[0]
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = "recovery"
    started = MigrationJournal.started(migration, "recovery")
    store.journal[1] = started
    store.execute_ddl(migration.payload)
    result = apply_migrations(manifest(), store, attempt_id="recovery")
    assert result.ready is True
    assert store.journal[1].state == "applied"


def test_same_owner_resumes_durable_ddl_observed_to_applied() -> None:
    store = MemoryMigrationStore()
    migration = manifest().migrations[0]
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = "observed-owner"
    store.execute_ddl(migration.payload)
    store.journal[1] = replace(
        MigrationJournal.started(migration, "observed-owner"), state="ddl_observed"
    )

    result = apply_migrations(manifest(), store, attempt_id="observed-owner")

    assert result.ready is True
    assert store.journal[1].state == "applied"


def test_fresh_attempt_requires_explicit_expected_owner_for_crash_recovery() -> None:
    store = MemoryMigrationStore()
    migration = manifest().migrations[0]
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = "crashed-owner"
    pending = MigrationJournal.pending(migration, "crashed-owner")
    started = replace(pending, state="ddl_started")
    store.journal[1] = started
    store.execute_ddl(migration.payload)

    denied = apply_migrations(manifest(), store, attempt_id="fresh")
    assert denied.reason == "migration_lock_busy"

    recovered = apply_migrations(
        manifest(), store, attempt_id="fresh", recovery_owner_attempt_id="crashed-owner"
    )
    assert recovered.ready is True
    assert store.journal[1].state == "applied"


def test_transactional_failure_requires_explicit_owner_recovery_then_retries() -> None:
    store = MemoryMigrationStore()
    store.fail_transactional = True

    failed = apply_migrations(manifest(), store, attempt_id="crashed-owner")

    assert failed.reason == "operator_hold_unexpected_store_failure"
    assert store.owner == "crashed-owner"
    assert sorted(store.journal) == list(range(1, 23))
    store.fail_transactional = False
    assert apply_migrations(manifest(), store, attempt_id="fresh").reason == "migration_lock_busy"

    recovered = apply_migrations(
        manifest(),
        store,
        attempt_id="fresh",
        recovery_owner_attempt_id="crashed-owner",
    )

    assert recovered.ready is True
    assert store.owner is None
    assert store.journal[23].state == "applied"


def test_applied_transactional_marker_allows_explicit_stale_owner_recovery() -> None:
    store = MemoryMigrationStore()
    assert apply_migrations(manifest(), store, attempt_id="crashed-owner").ready is True
    store.owner = "crashed-owner"

    assert apply_migrations(manifest(), store, attempt_id="fresh").reason == "migration_lock_busy"
    recovered = apply_migrations(
        manifest(),
        store,
        attempt_id="fresh",
        recovery_owner_attempt_id="crashed-owner",
    )

    assert recovered.ready is True
    assert store.owner is None
    assert len(store.transactional_calls) == 1


def test_applied_checksum_drift_fails_closed_without_mutation() -> None:
    store = MemoryMigrationStore()
    apply_migrations(manifest(), store, attempt_id="attempt-a")
    before = store.mutations
    store.journal[1] = replace(store.journal[1], checksum="0" * 64)
    result = apply_migrations(manifest(), store, attempt_id="attempt-b")
    assert result.reason == "checksum_mismatch"
    assert store.mutations == before
