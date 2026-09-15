from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_migrations import MemoryMigrationStore

from devgraph.model.base import WorkStatus
from devgraph.model.work import Task
from devgraph.ops.migrate import apply_migrations, load_manifest, readiness_status
from devgraph.storage.base import HealthStatus, NodeRecord, StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage

ROOT = Path(__file__).parents[2]


def _manifest():
    return load_manifest(ROOT / "migrations/manifest.json")


def _clean_store():
    manifest = _manifest()
    store = MemoryMigrationStore()
    assert apply_migrations(manifest, store, attempt_id="readiness-setup").ready is True
    return manifest, store


class UnavailableStorage(MemoryGraphStorage):
    def health(self) -> HealthStatus:
        return HealthStatus(live=True, ready=False, detail="raw storage detail")


class ArchiveMismatchStorage(MemoryGraphStorage):
    def inspect_canonical_persistence(self) -> None:
        raise StorageUnavailable("archive status mismatch")

    def query(
        self,
        label=None,
        archived=None,
        *,
        descending=False,
        after_id=None,
        limit=None,
    ):
        if label in (None, "Task"):
            task = Task(id="task-archived", title="Archived", status=WorkStatus.ARCHIVED)
            return [NodeRecord("Task", task.id, task.to_node_properties(), False)]
        return []


class InspectorFailureStorage(MemoryGraphStorage):
    def __init__(self) -> None:
        super().__init__()
        self.inspection_calls = 0

    def inspect_canonical_persistence(self) -> None:
        self.inspection_calls += 1
        raise StorageUnavailable("raw corruption detail must not leak")

    def query(self, *args, **kwargs):
        return []


def test_readiness_fails_closed_without_migration_configuration() -> None:
    status = readiness_status(None, None, MemoryGraphStorage())

    assert status.safe_output() == {
        "applied": [],
        "current_applied_version": 0,
        "manifest_schema_version": 0,
        "maximum_schema_version": 0,
        "minimum_schema_version": 0,
        "ready": False,
        "reason": "migration_configuration_absent",
    }


def test_readiness_fails_closed_when_canonical_storage_is_unavailable() -> None:
    manifest, store = _clean_store()

    status = readiness_status(manifest, store, UnavailableStorage())

    assert status.ready is False
    assert status.reason == "canonical_storage_unavailable"
    assert "raw storage detail" not in str(status.safe_output())


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda store, manifest: setattr(store, "owner", "active"), "migration_lock_busy"),
        (lambda store, manifest: store.journal.pop(23), "unapplied_migrations"),
        (
            lambda store, manifest: store.journal.__setitem__(
                1, replace(store.journal[1], checksum="0" * 64)
            ),
            "checksum_mismatch",
        ),
        (
            lambda store, manifest: store.journal.__setitem__(
                27, replace(store.journal[26], version=27)
            ),
            "operator_hold_journal_version_out_of_range",
        ),
    ],
)
def test_readiness_rejects_unclean_or_incompatible_migrations(mutate, reason: str) -> None:
    manifest, store = _clean_store()
    mutate(store, manifest)

    status = readiness_status(manifest, store, MemoryGraphStorage())

    assert status.ready is False
    assert status.reason == reason


def test_readiness_rejects_schema_below_minimum_version() -> None:
    manifest, store = _clean_store()
    store.journal.clear()

    status = readiness_status(manifest, store, MemoryGraphStorage())

    assert status.ready is False
    assert status.reason == "unapplied_migrations"
    assert status.current_applied_version == 0
    assert status.minimum_schema_version == 1


def test_readiness_rejects_missing_kind_under_canonical_label() -> None:
    manifest, store = _clean_store()
    storage = MemoryGraphStorage()
    storage.create_node("Task", "task-missing-kind", {"title": "legacy partial"})

    status = readiness_status(manifest, store, storage)

    assert status.ready is False
    assert status.reason == "canonical_persistence_invalid"


def test_readiness_rejects_archive_status_flag_mismatch() -> None:
    manifest, store = _clean_store()

    status = readiness_status(manifest, store, ArchiveMismatchStorage())

    assert status.ready is False
    assert status.reason == "canonical_persistence_invalid"


def test_readiness_uses_dedicated_canonical_inspection_seam() -> None:
    manifest, store = _clean_store()
    storage = InspectorFailureStorage()

    status = readiness_status(manifest, store, storage)

    assert status.ready is False
    assert status.reason == "canonical_persistence_invalid"
    assert storage.inspection_calls == 1


def test_readiness_is_true_only_for_clean_migrations_and_canonical_storage() -> None:
    manifest, store = _clean_store()
    storage = MemoryGraphStorage()
    task = Task(id="task-1", title="Ready")
    stored = storage.create_node("Task", task.id, task.to_node_properties())
    storage._nodes[("Task", task.id)] = replace(
        stored,
        properties={**stored.properties, "future_field": "persisted-forward-compatible"},
    )
    mutations_before = store.mutations

    status = readiness_status(manifest, store, storage)

    assert status.ready is True
    assert status.reason == "clean"
    assert status.current_applied_version == 26
    assert store.mutations == mutations_before
