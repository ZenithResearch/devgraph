from __future__ import annotations

import pytest

from devgraph.health import liveness, readiness
from devgraph.model.base import WorkStatus
from devgraph.model.validation import InvalidWorkObjectId
from devgraph.model.work import Task
from devgraph.storage.base import EdgeRecord, HealthStatus, NodeRecord, StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.migrations import load_constraint_statements


def storage_instances():
    return [pytest.param(MemoryGraphStorage(), id="memory")]


@pytest.mark.parametrize("storage", storage_instances())
def test_storage_crud_edges_archive_and_query_parity(storage):
    created = storage.create_node("Todo", "todo-1", {"title": "write storage", "status": "active"})

    assert created == NodeRecord(
        label="Todo",
        id="todo-1",
        properties={"title": "write storage", "status": "active"},
        archived=False,
    )
    assert storage.get_node("Todo", "todo-1") == created

    updated = storage.update_node("Todo", "todo-1", {"status": "done"})
    assert updated.properties == {"title": "write storage", "status": "done"}

    storage.create_node("Decision", "decision-1", {"title": "accept storage boundary"})
    edge = storage.create_edge(
        "Todo", "todo-1", "ACCEPTED_BY_DECISION", "Decision", "decision-1", {"source": "test"}
    )
    assert edge == EdgeRecord(
        from_label="Todo",
        from_id="todo-1",
        relationship="ACCEPTED_BY_DECISION",
        to_label="Decision",
        to_id="decision-1",
        properties={"source": "test"},
    )

    assert storage.query(label="Todo") == [updated]
    assert storage.query(label="Todo", archived=False) == [updated]

    archived = storage.archive_node("Todo", "todo-1")
    assert archived.archived is True
    post_archive = storage.update_node("Todo", "todo-1", {"title": "generic-update"})
    assert post_archive.archived is True
    assert post_archive.properties["title"] == "generic-update"
    assert storage.query(label="Todo", archived=False) == []
    assert storage.query(label="Todo", archived=True) == [post_archive]


@pytest.mark.parametrize("storage", storage_instances())
def test_storage_rejects_duplicate_nodes_and_missing_edges(storage):
    storage.create_node("Todo", "todo-1", {})

    with pytest.raises(KeyError, match="already exists"):
        storage.create_node("Todo", "todo-1", {})

    with pytest.raises(KeyError, match="missing from node"):
        storage.create_edge("Todo", "missing", "DEPENDS_ON", "Todo", "todo-1")

    with pytest.raises(KeyError, match="missing to node"):
        storage.create_edge("Todo", "todo-1", "DEPENDS_ON", "Todo", "missing")


def test_memory_storage_validates_ids_and_canonical_properties_before_write() -> None:
    storage = MemoryGraphStorage()
    with pytest.raises(InvalidWorkObjectId):
        storage.create_node("EventReceipt", "BAD_id", {"state": "pending"})

    task = Task(id="task-1", title="Canonical")
    malformed = task.to_node_properties()
    malformed["external_link_ids"] = ["BAD_id"]
    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.create_node(task.kind, task.id, malformed)

    assert storage.query("EventReceipt") == []
    assert storage.get_node(task.kind, task.id) is None


def test_memory_canonical_rearchive_is_immutable() -> None:
    storage = MemoryGraphStorage()
    task = Task(id="task-1", title="Canonical")
    storage.create_node(task.kind, task.id, task.to_node_properties())
    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.archive_node(task.kind, task.id)
    active = storage.get_node(task.kind, task.id)
    assert active is not None
    assert active.archived is False

    archived = task.with_status(WorkStatus.ARCHIVED)
    storage.archive_node(task.kind, task.id, archived.to_node_properties())
    rearchived = archived.with_status(WorkStatus.ARCHIVED)

    with pytest.raises(KeyError, match="already archived"):
        storage.archive_node(task.kind, task.id, rearchived.to_node_properties())

    record = storage.get_node(task.kind, task.id)
    assert record is not None
    assert record.properties == archived.to_node_properties()


def test_memory_canonical_update_enforces_creation_identity_and_version_cas() -> None:
    storage = MemoryGraphStorage()
    task = Task(id="task-cas", title="CAS")
    storage.create_node(task.kind, task.id, task.to_node_properties())

    changed_identity = task.with_status(WorkStatus.REVIEW).to_node_properties()
    changed_identity["created_at"] = changed_identity["updated_at"]
    with pytest.raises(KeyError, match="stale or inconsistent"):
        storage.update_node(task.kind, task.id, changed_identity)

    skipped_version = task.with_status(WorkStatus.REVIEW).to_node_properties()
    skipped_version["version"] = 3
    with pytest.raises(KeyError, match="stale or inconsistent"):
        storage.update_node(task.kind, task.id, skipped_version)

    changed_archive_identity = task.with_status(WorkStatus.ARCHIVED).to_node_properties()
    changed_archive_identity["created_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(KeyError, match="stale, or inconsistent"):
        storage.archive_node(task.kind, task.id, changed_archive_identity)

    record = storage.get_node(task.kind, task.id)
    assert record is not None
    assert record.properties == task.to_node_properties()


def test_memory_duplicate_node_creation_raises_key_error() -> None:
    storage = MemoryGraphStorage()
    storage.create_node("EventReceipt", "event-receipt-1", {"state": "pending"})

    with pytest.raises(KeyError, match="already exists"):
        storage.create_node("EventReceipt", "event-receipt-1", {"state": "duplicate"})


def test_memory_legacy_status_archive_transition_sets_archive_flag() -> None:
    storage = MemoryGraphStorage()
    task = Task(id="task-legacy", title="Legacy")
    storage.create_node(task.kind, task.id, task.to_node_properties())
    archived = task.with_status(WorkStatus.ARCHIVED)

    transitioned = storage.update_node(
        task.kind, task.id, archived.to_node_properties()
    )
    assert transitioned.archived is True
    assert storage.archive_node(task.kind, task.id) == transitioned


def test_generic_archive_and_post_archive_update_match_neo4j_semantics() -> None:
    storage = MemoryGraphStorage()
    storage.create_node("EventReceipt", "event-receipt-1", {"state": "pending"})

    archived = storage.archive_node(
        "EventReceipt", "event-receipt-1", {"archive_reason": "complete"}
    )
    assert archived.archived is True
    assert archived.properties == {"state": "pending", "archive_reason": "complete"}

    updated = storage.update_node(
        "EventReceipt", "event-receipt-1", {"state": "post-archive"}
    )
    assert updated.archived is True
    assert updated.properties == {
        "state": "post-archive",
        "archive_reason": "complete",
    }


def test_default_generic_query_is_not_silently_truncated() -> None:
    storage = MemoryGraphStorage()
    for index in range(101):
        node_id = f"event-receipt-{index:03d}"
        storage.create_node("EventReceipt", node_id, {"sequence": index})

    records = storage.query("EventReceipt", archived=False)

    assert len(records) == 101
    assert records[0].id == "event-receipt-000"
    assert records[-1].id == "event-receipt-100"


@pytest.mark.parametrize("storage", storage_instances())
def test_transaction_rolls_back_on_exception_and_commits_on_success(storage):
    with pytest.raises(RuntimeError, match="boom"):
        with storage.transaction():
            storage.create_node("Todo", "rolled-back", {"title": "temporary"})
            raise RuntimeError("boom")

    assert storage.get_node("Todo", "rolled-back") is None

    with storage.transaction():
        storage.create_node("Todo", "committed", {"title": "kept"})

    assert storage.get_node("Todo", "committed") is not None


@pytest.mark.parametrize("storage", storage_instances())
def test_constraint_runner_is_idempotent(storage):
    statements = load_constraint_statements()
    first = storage.apply_constraints(statements)
    second = storage.apply_constraints(statements)

    assert first.applied_count > 0
    assert second.applied_count == 0
    assert second.existing_count == first.applied_count


@pytest.mark.parametrize("storage", storage_instances())
def test_health_and_readiness_fail_closed(storage):
    assert storage.health() == HealthStatus(live=True, ready=True, detail="memory storage ready")
    assert liveness(storage).live is True
    assert readiness(storage).ready is True

    class DownStorage:
        def health(self):
            return HealthStatus(live=True, ready=False, detail="canonical storage unavailable")

    assert liveness(DownStorage()).live is True
    assert readiness(DownStorage()).ready is False

    class ExplodingStorage:
        def health(self):
            raise StorageUnavailable("cannot connect with raw-secret")

    live_status = liveness(ExplodingStorage())
    assert live_status.live is True
    assert live_status.detail == "storage healthcheck failed"
    status = readiness(ExplodingStorage())
    assert status.ready is False
    assert status.detail == "storage healthcheck failed"
