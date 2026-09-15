from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from typing import Any, cast

import pytest

from devgraph.model.base import WorkStatus
from devgraph.model.lifecycle import ProposalLifecycle, ProposalLifecycleError
from devgraph.model.repository import WORK_OBJECT_TYPES, WorkObjectRepository
from devgraph.model.work import Decision, Proposal, Task
from devgraph.ops.migrate import load_manifest
from devgraph.storage import neo4j as neo4j_module
from devgraph.storage.base import EdgeRecord, NodeRecord, StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.neo4j import Neo4jGraphStorage, Neo4jMigrationStore

ROOT = Path(__file__).parents[2]


def _storage_with_rows(rows: list[dict[str, object]]):
    storage = object.__new__(Neo4jGraphStorage)
    calls: list[tuple[str, dict[str, object]]] = []

    def run_graph(self, query: str, **parameters: object):
        calls.append((query, parameters))
        return rows

    storage._run_graph = MethodType(run_graph, storage)
    return storage, calls


def _storage_with_row_batches(*batches: list[dict[str, object]]):
    storage = object.__new__(Neo4jGraphStorage)
    calls: list[tuple[str, dict[str, object]]] = []

    def run_graph(self, query: str, **parameters: object):
        calls.append((query, parameters))
        return batches[len(calls) - 1]

    storage._run_graph = MethodType(run_graph, storage)
    return storage, calls


def _canonical_row(*, labels: list[str] | None = None, archived: object = False):
    task = Task(id="task-1", title="Canonical", priority=3)
    return {
        "labels": labels or ["Task"],
        "id": task.id,
        "archived": archived,
        "properties": {"id": task.id, "archived": archived, **task.to_node_properties()},
    }


@pytest.mark.parametrize("kind, work_class", WORK_OBJECT_TYPES.items())
def test_all_work_kinds_hydrate_identically_in_memory_and_simulated_neo4j(
    kind, work_class
) -> None:
    work_object = work_class(
        id=f"{kind.lower()}-1",
        title=f"Canonical {kind}",
        priority=3,
        artifact_ids=("artifact-1",),
        external_link_ids=("external-link-1",),
    )
    row = {
        "labels": [kind],
        "id": work_object.id,
        "archived": False,
        "properties": {
            "id": work_object.id,
            "archived": False,
            **work_object.to_node_properties(),
        },
    }
    neo4j_storage, _ = _storage_with_rows([row])
    neo4j_loaded = WorkObjectRepository(neo4j_storage).get_by_id(kind, work_object.id)

    memory_repository = WorkObjectRepository(MemoryGraphStorage())
    memory_loaded = memory_repository.create(work_object)

    assert neo4j_loaded == memory_loaded == work_object


def _generic_row(
    *,
    label: str = "EventReceipt",
    node_id: str = "event-receipt-1",
    archived: bool = False,
    properties: dict[str, object] | None = None,
) -> dict[str, object]:
    generic_properties = {"state": "pending"} if properties is None else properties
    return {
        "labels": [label],
        "id": node_id,
        "archived": archived,
        "properties": {"id": node_id, "archived": archived, **generic_properties},
    }


def test_proposal_lifecycle_rehydration_preserves_creation_identity() -> None:
    proposal = Proposal(id="proposal-1", title="Preserve identity")
    node = NodeRecord(
        proposal.kind,
        proposal.id,
        proposal.to_node_properties(),
        False,
    )

    rehydrated = ProposalLifecycle._proposal_from_node(node)

    assert rehydrated.created_at == proposal.created_at
    assert rehydrated.updated_at == proposal.updated_at


def test_proposal_lifecycle_rehydration_rejects_nonproposal_and_archive_mismatch() -> None:
    proposal = Proposal(id="proposal-1", title="Strict")
    properties = proposal.to_node_properties()

    with pytest.raises(ProposalLifecycleError):
        ProposalLifecycle._proposal_from_node(
            NodeRecord("Task", proposal.id, properties, False)
        )
    with pytest.raises(ProposalLifecycleError):
        ProposalLifecycle._proposal_from_node(
            NodeRecord("Proposal", proposal.id, properties, True)
        )


def test_proposal_lifecycle_rejects_initially_accepted_proposal() -> None:
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(
        id="proposal-accepted",
        title="Must require provenance",
        status=WorkStatus.ACCEPTED,
    )

    with pytest.raises(ProposalLifecycleError, match="Decision provenance"):
        lifecycle.create_proposal(proposal)

    assert storage.get_node("Proposal", proposal.id) is None


def test_proposal_acceptance_rejects_malformed_existing_decision() -> None:
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Needs valid provenance")
    decision = Decision(id="decision-1", title="Accept")
    lifecycle.create_proposal(proposal)
    storage.create_node("Decision", decision.id, {"title": "malformed"})

    with pytest.raises(ProposalLifecycleError, match="malformed Decision provenance"):
        lifecycle.accept_proposal(proposal.id, decision)

    assert lifecycle.get_proposal(proposal.id).status == WorkStatus.DRAFT
    assert storage.list_edges("ACCEPTED_BY_DECISION") == []


def test_generic_node_operations_preserve_safe_protocol_substitutability() -> None:
    properties = {
        "state": "pending",
        "attempts": 0,
        "kind": "delivery",
        "redacted_summary": {"safe": 1, "nested": {"value": True}},
    }
    storage, calls = _storage_with_rows([_generic_row(properties=properties)])

    assert storage.create_node("EventReceipt", "event-receipt-1", properties) == NodeRecord(
        "EventReceipt", "event-receipt-1", properties, False
    )
    create_parameters = calls[-1][1]
    persisted_properties = cast(dict[str, object], create_parameters["properties"])
    assert create_parameters["node_id"] == "event-receipt-1"
    assert set(persisted_properties) == set(properties) | {
        "__devgraph_generic_encoding"
    }
    assert persisted_properties["__devgraph_generic_encoding"] == "json-v1"
    assert all(isinstance(value, str) for value in persisted_properties.values())

    assert storage.get_node("EventReceipt", "event-receipt-1") == NodeRecord(
        "EventReceipt", "event-receipt-1", properties, False
    )

    encoded_properties = Neo4jGraphStorage._generic_properties(properties)
    storage, _ = _storage_with_rows(
        [_generic_row(properties=encoded_properties)]
    )
    assert storage.get_node("EventReceipt", "event-receipt-1") == NodeRecord(
        "EventReceipt", "event-receipt-1", properties, False
    )

    updated_properties = {
        "state": "retry-scheduled",
        "attempts": 1,
        "kind": "delivery",
        "redacted_summary": {"safe": 2, "nested": {"value": False}},
    }
    storage, calls = _storage_with_rows(
        [_generic_row(properties=updated_properties)]
    )
    assert storage.update_node(
        "EventReceipt", "event-receipt-1", updated_properties
    ).properties == updated_properties
    updated_persisted = cast(
        dict[str, object], calls[-1][1]["replacement_properties"]
    )
    assert set(updated_persisted) == set(updated_properties) | {
        "id",
        "archived",
        "__devgraph_generic_encoding",
    }
    assert updated_persisted["id"] == "event-receipt-1"
    assert updated_persisted["archived"] is False
    assert updated_persisted["__devgraph_generic_encoding"] == "json-v1"
    assert all(
        isinstance(updated_persisted[key], str) for key in updated_properties
    )
    assert "n.kind IS NULL" not in calls[-1][0]

    storage, calls = _storage_with_rows(
        [_generic_row(archived=True, properties=updated_properties)]
    )
    assert storage.archive_node("EventReceipt", "event-receipt-1").archived is True
    assert "n.archived = true" in calls[-1][0]
    assert "n.kind IS NULL" not in calls[-1][0]


def test_generic_create_rejects_existing_identity_without_mutation() -> None:
    storage, calls = _storage_with_rows([])

    with pytest.raises(KeyError, match="already exists"):
        storage.create_node(
            "EventReceipt", "event-receipt-1", {"state": "pending"}
        )

    assert "MERGE (n:`EventReceipt`" in calls[-1][0]
    assert "ON CREATE SET" in calls[-1][0]
    assert "WHERE created" in calls[-1][0]


@pytest.mark.parametrize("created", [True, False])
def test_event_receipt_claim_uses_one_unique_property_merge(created: bool) -> None:
    digest = "a" * 64
    properties = {"idempotency_claim_digest": digest, "state": "pending"}
    encoded = Neo4jGraphStorage._generic_properties(properties)
    row = _generic_row(
        node_id="event-receipt-existing" if not created else "event-receipt-new",
        properties=encoded,
    )
    row["created"] = created
    storage, calls = _storage_with_rows([row])

    claim = storage.claim_event_receipt(
        "event-receipt-new",
        digest,
        properties,
    )

    assert claim.created is created
    assert claim.receipt.properties == properties
    query, parameters = calls[-1]
    assert "MERGE (n:`EventReceipt` {idempotency_claim_digest: $claim_digest})" in query
    assert "coalesce(n.__devgraph_create_nonce = $create_nonce, false)" in query
    assert parameters["claim_digest"] == encoded["idempotency_claim_digest"]
    assert parameters["properties"] == encoded


def test_event_receipt_claim_rejects_mismatched_digest_before_storage() -> None:
    storage, calls = _storage_with_rows([])

    with pytest.raises(StorageUnavailable, match="invalid idempotency claim"):
        storage.claim_event_receipt(
            "event-receipt-1",
            "a" * 64,
            {"idempotency_claim_digest": "b" * 64},
        )

    assert calls == []


def test_legacy_generic_sentinel_string_is_not_decoded_without_node_marker() -> None:
    properties: dict[str, object] = {
        "message": '__devgraph_generic_json_v1__:{"legacy":true}'
    }
    storage, _ = _storage_with_rows([_generic_row(properties=properties)])

    record = storage.get_node("EventReceipt", "event-receipt-1")

    assert record == NodeRecord("EventReceipt", "event-receipt-1", properties, False)


def test_legacy_generic_update_atomically_converts_all_properties() -> None:
    existing: dict[str, object] = {
        "message": '__devgraph_generic_json_v1__:{"legacy":true}',
        "state": "pending",
    }
    merged = {**existing, "state": "retry-scheduled"}
    encoded = Neo4jGraphStorage._generic_properties(merged)
    storage, calls = _storage_with_row_batches(
        [_generic_row(properties=existing)],
        [_generic_row(properties=encoded)],
    )

    record = storage.update_node(
        "EventReceipt", "event-receipt-1", {"state": "retry-scheduled"}
    )

    assert record.properties == merged
    assert len(calls) == 2
    assert "properties(n) = $expected_properties" in calls[1][0]
    assert calls[1][1]["expected_properties"] == {
        "id": "event-receipt-1",
        "archived": False,
        **existing,
    }


def test_legacy_generic_archive_with_properties_atomically_converts_all_properties() -> None:
    existing: dict[str, object] = {
        "message": '__devgraph_generic_json_v1__:{"legacy":true}',
        "state": "pending",
    }
    merged = {**existing, "state": "failed"}
    encoded = Neo4jGraphStorage._generic_properties(merged)
    storage, calls = _storage_with_row_batches(
        [_generic_row(properties=existing)],
        [_generic_row(archived=True, properties=encoded)],
    )

    record = storage.archive_node(
        "EventReceipt", "event-receipt-1", {"state": "failed"}
    )

    assert record.archived is True
    assert record.properties == merged
    assert len(calls) == 2
    assert "properties(n) = $expected_properties" in calls[1][0]


def test_health_does_not_expose_raw_driver_exception() -> None:
    class UnavailableDriver:
        def verify_connectivity(self) -> None:
            raise neo4j_module.ServiceUnavailable(
                "bolt://internal.example?credential=raw-secret"
            )

    storage = object.__new__(Neo4jGraphStorage)
    cast(Any, storage)._driver = UnavailableDriver()

    status = storage.health()

    assert status.live is True
    assert status.ready is False
    assert status.detail == "Neo4j storage unavailable"


def test_generic_partial_payload_under_work_label_remains_generic() -> None:
    properties: dict[str, object] = {"title": "legacy todo", "status": "active"}
    row = _generic_row(label="Todo", node_id="todo-1", properties=properties)
    storage, calls = _storage_with_rows([row])

    created = storage.create_node("Todo", "todo-1", properties)
    assert created == NodeRecord("Todo", "todo-1", properties, False)
    assert "n.kind IS NULL" not in calls[-1][0]

    updated_properties: dict[str, object] = {
        "title": "legacy todo",
        "status": "done",
    }
    updated_row = _generic_row(
        label="Todo", node_id="todo-1", properties=updated_properties
    )
    storage, calls = _storage_with_rows([updated_row])
    updated = storage.update_node("Todo", "todo-1", updated_properties)
    assert updated.properties == updated_properties
    assert "AND n.kind IS NULL" in calls[-1][0]

    archived_row = _generic_row(
        label="Todo",
        node_id="todo-1",
        archived=True,
        properties=updated_properties,
    )
    storage, calls = _storage_with_rows([archived_row])
    archived = storage.archive_node("Todo", "todo-1")
    assert archived.archived is True
    assert "n.kind IS NULL OR n.status = 'archived'" in calls[-1][0]


def test_generic_query_and_edges_accept_safe_non_work_labels() -> None:
    row = _generic_row()
    storage, calls = _storage_with_rows([row])

    assert storage.query("EventReceipt", archived=False) == [
        NodeRecord("EventReceipt", "event-receipt-1", {"state": "pending"}, False)
    ]
    assert "LIMIT" not in calls[-1][0]

    edge_row = {
        "from_labels": ["Task"],
        "from_id": "task-1",
        "relationship": "EMITTED_EVENT",
        "to_labels": ["EventReceipt"],
        "to_id": "event-receipt-1",
        "properties": {},
    }
    storage, _ = _storage_with_rows([edge_row])
    assert storage.create_edge(
        "Task", "task-1", "EMITTED_EVENT", "EventReceipt", "event-receipt-1"
    ).to_label == "EventReceipt"


@pytest.mark.parametrize("label", ["eventReceipt", "Event Receipt", "EventReceipt` MATCH (n)"])
def test_generic_operations_reject_unsafe_label_syntax(label: str) -> None:
    storage, calls = _storage_with_rows([])

    with pytest.raises(StorageUnavailable, match="invalid node label"):
        storage.create_node(label, "event-receipt-1", {"state": "pending"})

    assert calls == []


def test_legacy_work_update_then_archive_sequence_remains_supported() -> None:
    task = Task(id="task-1", title="Canonical")
    archived = Task(
        id=task.id,
        title=task.title,
        status=WorkStatus.ARCHIVED,
        created_at=task.created_at,
        updated_at=task.updated_at,
        version=2,
    )
    archived_row = _canonical_row(archived=True)
    archived_row["properties"].update(archived.to_node_properties())
    storage, calls = _storage_with_rows([archived_row])

    assert storage.update_node("Task", task.id, archived.to_node_properties()).archived is True
    assert "n.archived = true" in calls[-1][0]

    assert storage.archive_node("Task", task.id).archived is True
    assert "n.archived = false" not in calls[-1][0]


def test_cross_label_query_is_deterministic_and_unbounded_by_default() -> None:
    rows = [_generic_row(), _canonical_row()]
    storage, calls = _storage_with_rows(rows)

    records = storage.query(label=None, archived=None)

    assert [(record.label, record.id) for record in records] == [
        ("EventReceipt", "event-receipt-1"),
        ("Task", "task-1"),
    ]
    query, parameters = calls[-1]
    assert "MATCH (n)" in query
    assert "n.id IS NOT NULL AND n.archived IS NOT NULL" in query
    assert "ORDER BY labels(n)[0] ASC, n.id ASC" in query
    assert "LIMIT" not in query
    assert parameters == {"archived": None, "after_id": None}


def test_owner_only_recovery_uses_terminal_journal_guard() -> None:
    storage, _ = _storage_with_rows([])
    store = Neo4jMigrationStore(storage)
    calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any):
        calls.append((query, parameters))
        return [{"recovered": 1}]

    store._run = MethodType(run, store)

    assert store.recover_owner("crashed-owner", "fresh-owner", None) is True

    query, parameters = calls[-1]
    assert "owner_attempt_id: $expected" in query
    assert "NOT EXISTS" in query
    assert "recoverable_ddl_not_applied" in query
    assert "MATCH (j:DevgraphMigration {version:" not in query
    assert parameters == {
        "expected": "crashed-owner",
        "new_attempt": "fresh-owner",
    }


def test_edges_parameterize_application_values_and_hydrate_strictly() -> None:
    row = {
        "from_labels": ["Task"],
        "from_id": "task-1",
        "relationship": "BLOCKS",
        "to_labels": ["Task"],
        "to_id": "task-2",
        "properties": {"reason": "dependency-marker"},
    }
    storage, calls = _storage_with_rows([row])

    created = storage.create_edge(
        "Task",
        "task-1",
        "BLOCKS",
        "Task",
        "task-2",
        {"reason": "dependency-marker"},
    )
    assert created == EdgeRecord(
        "Task", "task-1", "BLOCKS", "Task", "task-2", {"reason": "dependency-marker"}
    )
    query, parameters = calls[-1]
    assert "task-1" not in query
    assert "task-2" not in query
    assert "dependency-marker" not in query
    edge_identity = parameters.pop("edge_identity")
    assert isinstance(edge_identity, str)
    assert len(edge_identity) == 64
    assert edge_identity not in query
    assert parameters == {
        "from_id": "task-1",
        "to_id": "task-2",
        "properties": {"reason": "dependency-marker"},
    }

    listed = storage.list_edges("BLOCKS")
    assert listed == [created]
    assert calls[-1][1] == {"relationship": "BLOCKS"}


def test_edge_creation_merges_only_identical_property_identity() -> None:
    row = {
        "from_labels": ["Task"],
        "from_id": "task-1",
        "relationship": "BLOCKS",
        "to_labels": ["Task"],
        "to_id": "task-2",
        "properties": {"reason": "dependency-marker"},
    }
    storage, calls = _storage_with_rows([row])

    edge = storage.create_edge(
        "Task",
        "task-1",
        "BLOCKS",
        "Task",
        "task-2",
        {"reason": "dependency-marker"},
    )

    assert edge.properties == {"reason": "dependency-marker"}
    assert "MERGE (source)-[edge:`BLOCKS`" in calls[-1][0]
    assert "__devgraph_edge_identity" in calls[-1][0]
    assert isinstance(calls[-1][1]["edge_identity"], str)


@pytest.mark.parametrize("relationship", ["blocks", "BLOCKS]-(x)", "", "BLOCKS SPACE"])
def test_edges_reject_unsafe_relationship_syntax(relationship: str) -> None:
    storage, calls = _storage_with_rows([])
    with pytest.raises(StorageUnavailable, match="invalid relationship"):
        storage.create_edge("Task", "task-1", relationship, "Task", "task-2")
    assert calls == []


def test_get_uses_parameter_only_application_id_and_excludes_storage_properties() -> None:
    row = _canonical_row()
    row["properties"]["unknown_future_field"] = "must-not-leak"
    storage, calls = _storage_with_rows([row])

    record = storage.get_node("Task", "task-1")

    expected_properties = {
        key: value
        for key, value in row["properties"].items()
        if key not in {"id", "archived", "unknown_future_field"}
    }
    assert record == NodeRecord(
        label="Task",
        id="task-1",
        properties=expected_properties,
        archived=False,
    )
    query, parameters = calls[0]
    assert "task-1" not in query
    assert parameters["node_id"] == "task-1"
    assert "elementId" not in query


def test_get_rejects_multiple_work_kind_labels() -> None:
    storage, _ = _storage_with_rows([_canonical_row(labels=["Task", "Issue"])])

    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.get_node("Task", "task-1")


def test_get_rejects_kind_label_id_and_archive_mismatch() -> None:
    cases = [
        _canonical_row(labels=["Issue"]),
        {**_canonical_row(), "id": "other"},
        _canonical_row(archived="false"),
        _canonical_row(archived=True),
    ]
    for row in cases:
        storage, _ = _storage_with_rows([row])
        with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
            storage.get_node("Task", "task-1")


@pytest.mark.parametrize("limit", [1, 100])
def test_query_uses_bounded_parameterized_id_keyset(limit: int) -> None:
    storage, calls = _storage_with_rows([])

    assert storage.query(
        label="Task", archived=False, descending=True, after_id="task-9", limit=limit
    ) == []
    query, parameters = calls[0]
    assert "task-9" not in query
    assert "$after_id" in query
    assert "ORDER BY n.id DESC" in query
    assert "LIMIT $limit" in query
    assert parameters == {"archived": False, "after_id": "task-9", "limit": limit}


def test_query_is_all_or_error_and_never_returns_partial_items() -> None:
    valid = _canonical_row()
    malformed = _canonical_row(labels=["Task", "Issue"])
    storage, _ = _storage_with_rows([valid, malformed])

    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.query(label="Task", archived=False)


def test_malformed_outbound_properties_raise_safe_storage_error() -> None:
    task = Task(id="task-1", title="Canonical")
    malformed = task.to_node_properties()
    malformed["priority"] = True
    storage, calls = _storage_with_rows([])

    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.create_node(task.kind, task.id, malformed)

    assert calls == []

    with_unknown = task.to_node_properties()
    with_unknown["future_field"] = "caller-controlled"
    with pytest.raises(StorageUnavailable, match="malformed canonical work object"):
        storage.create_node(task.kind, task.id, with_unknown)

    assert calls == []


def test_transaction_commit_driver_failure_raises_safe_storage_error() -> None:
    from devgraph.storage.neo4j import Neo4jError

    class CommitFailure(Neo4jError):
        pass

    class Transaction:
        rolled_back = False
        closed = False

        def commit(self) -> None:
            raise CommitFailure("driver detail must not escape")

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    transaction = Transaction()

    class Session:
        closed = False

        def begin_transaction(self):
            return transaction

        def close(self) -> None:
            self.closed = True

    session = Session()
    storage = object.__new__(Neo4jGraphStorage)
    storage._initialize_transaction_state()
    storage._session = MethodType(lambda self: session, storage)

    with pytest.raises(StorageUnavailable, match="canonical storage transaction failed"):
        with storage.transaction():
            pass

    assert transaction.rolled_back is True
    assert transaction.closed is True
    assert session.closed is True


def test_create_update_and_archive_keep_values_parameterized() -> None:
    task = Task(id="task-1", title="sensitive-title-marker", priority=3)
    create_row = _canonical_row()
    create_row["properties"].update(task.to_node_properties())
    storage, calls = _storage_with_rows([create_row])

    created = storage.create_node(task.kind, task.id, task.to_node_properties())
    assert created.id == task.id
    create_query, create_parameters = calls[-1]
    assert task.id not in create_query
    assert task.title not in create_query
    assert create_parameters["node_id"] == task.id
    assert create_parameters["properties"] == task.to_node_properties()

    updated_task = Task(
        id=task.id,
        title="updated-title-marker",
        priority=4,
        created_at=task.created_at,
        updated_at=task.updated_at,
        version=2,
    )
    update_row = _canonical_row()
    update_row["properties"].update(updated_task.to_node_properties())
    storage, calls = _storage_with_rows([update_row])
    updated = storage.update_node(task.kind, task.id, updated_task.to_node_properties())
    assert updated.properties["title"] == "updated-title-marker"
    update_query, update_parameters = calls[-1]
    assert updated_task.title not in update_query
    assert "id" not in update_parameters["properties"]
    assert "archived" not in update_parameters["properties"]
    assert "n.version = $expected_version" in update_query
    assert "n.status <> 'archived'" in update_query
    assert "n.archived = false" in update_query
    assert update_parameters["expected_version"] == 1

    archived_row = _canonical_row(archived=True)
    archived_row["properties"]["status"] = "archived"
    storage, calls = _storage_with_rows([archived_row])
    archived = storage.archive_node(task.kind, task.id)
    assert archived.archived is True
    archive_query, archive_parameters = calls[-1]
    assert task.id not in archive_query
    assert archive_parameters["node_id"] == task.id


def test_canonical_archive_is_one_versioned_cypher_write() -> None:
    active = Task(id="task-1", title="Archive")
    archived = Task(
        id=active.id,
        title=active.title,
        status=WorkStatus.ARCHIVED,
        created_at=active.created_at,
        updated_at=active.updated_at,
        version=2,
    )
    row = {
        "labels": ["Task"],
        "id": active.id,
        "archived": True,
        "properties": {
            "id": active.id,
            "archived": True,
            **archived.to_node_properties(),
        },
    }
    storage, calls = _storage_with_rows([row])

    result = storage.archive_node("Task", active.id, archived.to_node_properties())

    assert result.archived is True
    assert len(calls) == 1
    query, parameters = calls[0]
    assert "n.version = $expected_version" in query
    assert "SET n += $properties, n.archived = true" in query
    assert parameters["expected_version"] == 1


def test_repository_create_uses_one_storage_transaction() -> None:
    task = Task(id="task-1", title="transactional")
    row = _canonical_row()
    row["properties"].update(task.to_node_properties())
    storage, calls = _storage_with_rows([])
    responses = iter(([], [row]))

    def run_graph(self, query: str, **parameters: object):
        calls.append((query, parameters))
        return next(responses)

    storage._run_graph = MethodType(run_graph, storage)
    entered: list[str] = []

    @contextmanager
    def transaction(self):
        entered.append("begin")
        try:
            yield
        finally:
            entered.append("end")

    storage.transaction = MethodType(transaction, storage)
    created = WorkObjectRepository(storage).create(task)
    assert created == task
    assert entered == ["begin", "end"]


def test_transaction_routes_calls_through_one_driver_transaction_and_rolls_back() -> None:
    class Record:
        def __init__(self, data):
            self._data = data

        def data(self):
            return self._data

    class FakeTransaction:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.queries = []

        def run(self, query, **parameters):
            self.queries.append((query, parameters))
            return [Record({"ok": True})]

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    class FakeSession:
        def __init__(self, tx):
            self.tx = tx
            self.closed = False

        def begin_transaction(self):
            return self.tx

        def close(self):
            self.closed = True

    class FakeDriver:
        def __init__(self):
            self.transactions = []
            self.sessions = []

        def session(self, **_kwargs):
            tx = FakeTransaction()
            session = FakeSession(tx)
            self.transactions.append(tx)
            self.sessions.append(session)
            return session

    storage = object.__new__(Neo4jGraphStorage)
    driver = FakeDriver()
    storage._driver = cast(Any, driver)
    storage._config = type("Config", (), {"database": None})()
    storage._initialize_transaction_state()

    with storage.transaction():
        assert storage._run_graph("RETURN $value AS value", value=1) == [{"ok": True}]
    first = driver.transactions[0]
    assert first.commits == 1 and first.rollbacks == 0
    assert first.queries == [("RETURN $value AS value", {"value": 1})]

    with pytest.raises(RuntimeError, match="boom"):
        with storage.transaction():
            storage._run_graph("RETURN 2")
            raise RuntimeError("boom")
    second = driver.transactions[1]
    assert second.commits == 0 and second.rollbacks == 1

    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            try:
                with storage.transaction():
                    storage._run_graph("RETURN 3")
                    raise RuntimeError("nested boom")
            except RuntimeError:
                pass
    third = driver.transactions[2]
    assert third.commits == 0 and third.rollbacks == 1
    assert all(session.closed for session in driver.sessions)


class TransactionalMigrationStorage:
    def __init__(
        self,
        task_rows: list[dict[str, object]],
        *,
        journal_applied: int = 1,
    ) -> None:
        self.task_rows = task_rows
        self.journal_applied = journal_applied
        self.writes: list[tuple[str, object]] = []

    @contextmanager
    def transaction(self):
        before = list(self.writes)
        try:
            yield
        except Exception:
            self.writes = before
            raise

    def _run_graph(self, query: str, **parameters: object):
        if "RETURN labels(n) AS labels" in query:
            if "`Task`" in query:
                return self.task_rows
            return []
        if "SET n.archived = $archived" in query:
            self.writes.append(("backfill", (parameters["node_id"], parameters["archived"])))
            return [{"changed": 1}]
        if "CREATE (journal:DevgraphMigration" in query:
            if self.journal_applied == 1:
                self.writes.append(("journal", parameters["data"]))
            return [{"applied": self.journal_applied}]
        raise AssertionError(f"unexpected query: {query}")


def _legacy_task_row(*, status: str, kind: str = "Task") -> dict[str, object]:
    task = Task(id=f"task-{status}", title=status)
    properties = {"id": task.id, **task.to_node_properties()}
    properties["status"] = status
    properties["kind"] = kind
    return {"labels": ["Task"], "id": task.id, "properties": properties}


def test_v23_preflight_backfills_legacy_archive_and_journals_last_atomically() -> None:
    storage = TransactionalMigrationStorage(
        [_legacy_task_row(status="draft"), _legacy_task_row(status="archived")]
    )
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[22]
    store = Neo4jMigrationStore(cast(Neo4jGraphStorage, storage))

    assert store.apply_transactional_data(migration, "attempt-v23", "2026-01-01T00:00:00+00:00")
    assert storage.writes[0] == ("backfill", ("task-draft", False))
    assert storage.writes[1] == ("backfill", ("task-archived", True))
    assert storage.writes[2][0] == "journal"


def test_v23_zero_journal_result_rolls_back_backfills() -> None:
    storage = TransactionalMigrationStorage(
        [_legacy_task_row(status="draft")], journal_applied=0
    )
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[22]
    store = Neo4jMigrationStore(cast(Neo4jGraphStorage, storage))

    with pytest.raises(StorageUnavailable, match="journal marker failed"):
        store.apply_transactional_data(
            migration, "attempt-v23", "2026-01-01T00:00:00+00:00"
        )

    assert storage.writes == []


def test_v23_preflight_malformed_row_rolls_back_without_backfill_or_journal() -> None:
    storage = TransactionalMigrationStorage([_legacy_task_row(status="draft", kind="Issue")])
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[22]
    store = Neo4jMigrationStore(cast(Neo4jGraphStorage, storage))

    with pytest.raises(StorageUnavailable, match="canonical persistence preflight failed"):
        store.apply_transactional_data(migration, "attempt-v23", "2026-01-01T00:00:00+00:00")
    assert storage.writes == []


def test_v23_preflight_missing_kind_rolls_back_without_backfill_or_journal() -> None:
    row = _legacy_task_row(status="draft")
    row_properties = cast(dict[str, object], row["properties"])
    del row_properties["kind"]
    storage = TransactionalMigrationStorage([row])
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[22]
    store = Neo4jMigrationStore(cast(Neo4jGraphStorage, storage))

    with pytest.raises(StorageUnavailable, match="canonical persistence preflight failed"):
        store.apply_transactional_data(
            migration, "attempt-v23", "2026-01-01T00:00:00+00:00"
        )
    assert storage.writes == []


def test_repository_round_trip_over_simulated_neo4j_row() -> None:
    storage, _ = _storage_with_rows([_canonical_row()])
    repository = WorkObjectRepository(storage)

    loaded = repository.get_by_id("Task", "task-1")
    assert loaded.id == "task-1"
    assert loaded.title == "Canonical"
    assert loaded.priority == 3
    assert loaded.to_node_properties() == {
        key: value
        for key, value in storage.get_node("Task", "task-1").properties.items()
    }
