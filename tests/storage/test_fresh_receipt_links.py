"""Fresh receipt linking cannot be selected with a caller-supplied created flag."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.storage.test_neo4j_work_transactions import storage_fixture

from devgraph.storage.base import NodeRecord, StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.neo4j import Neo4jGraphStorage, _work_query_phase


def props(subject="issue-1", digest="a" * 64):
    return {"idempotency_claim_digest": digest, "subject_label": "Issue", "subject_id": subject}


def claim(storage, receipt="receipt-1", subject="issue-1", digest="a" * 64):
    return storage.claim_event_receipt(receipt, digest, props(subject, digest))


def test_memory_requires_owned_claim_and_consumes_it_once():
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-1", {})
    with storage.transaction():
        assert claim(storage).created
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "other", "receipt-1")
        edge = storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
        assert edge.relationship == "EMITTED_EVENT" and edge.properties == {}
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert len(storage.list_edges("EMITTED_EVENT")) == 1
    with storage.transaction():
        assert not claim(storage).created
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")


def test_memory_manual_or_standalone_receipt_does_not_mint_link_authority():
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-1", {})
    claim(storage)  # Historical standalone claims retain their existing behavior.
    with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    with storage.transaction():
        storage.create_node("EventReceipt", "manual", props())
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "manual")


@pytest.mark.parametrize("invalid", ["missing_subject", "replaced_receipt", "already_linked"])
def test_memory_invalid_endpoint_rolls_back_mutation_and_claim(invalid):
    storage = MemoryGraphStorage()
    with pytest.raises(StorageUnavailable):
        with storage.transaction():
            storage.create_node("Issue", "issue-1", {})
            claim(storage)
            if invalid == "missing_subject":
                del storage._nodes[("Issue", "issue-1")]
            elif invalid == "replaced_receipt":
                storage._nodes[("EventReceipt", "receipt-1")] = NodeRecord(
                    "EventReceipt", "receipt-1", props()
                )
            else:
                storage.create_edge(
                    "Issue", "issue-1", "EMITTED_EVENT", "EventReceipt", "receipt-1"
                )
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert not storage.query() and not storage.list_edges()
    assert not hasattr(storage._transaction_state, "fresh_receipt_claims")


def test_memory_nested_rollback_restores_only_preexisting_owned_proofs():
    storage = MemoryGraphStorage()
    with storage.transaction():
        storage.create_node("Issue", "issue-1", {})
        claim(storage)
        with pytest.raises(RuntimeError):
            with storage.transaction():
                claim(storage, "receipt-2", digest="b" * 64)
                raise RuntimeError("rollback inner claim")
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-2")
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert len(storage.query("EventReceipt")) == len(storage.list_edges("EMITTED_EVENT")) == 1


@pytest.mark.parametrize("failure", ["endpoint", "write"])
def test_memory_caught_link_failure_cannot_commit_partial_work(monkeypatch, failure):
    storage = MemoryGraphStorage()

    def fail_edge(*args, **kwargs):
        raise StorageUnavailable("edge write failed")

    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            storage.create_node("Issue", "issue-1", {})
            claim(storage)
            if failure == "endpoint":
                del storage._nodes[("Issue", "issue-1")]
            else:
                monkeypatch.setattr(storage, "create_edge", fail_edge)
            with pytest.raises(StorageUnavailable):
                storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert not storage.query() and not storage.list_edges()
    assert not hasattr(storage._transaction_state, "rollback_only")


def test_memory_nested_failed_link_rolls_back_to_savepoint():
    storage = MemoryGraphStorage()
    with storage.transaction():
        storage.create_node("Issue", "issue-1", {})
        claim(storage)
        with pytest.raises(StorageUnavailable, match="nested transaction failed"):
            with storage.transaction():
                del storage._nodes[("Issue", "issue-1")]
                with pytest.raises(StorageUnavailable):
                    storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert len(storage.query("Issue")) == len(storage.query("EventReceipt")) == 1
    assert len(storage.list_edges("EMITTED_EVENT")) == 1


def test_memory_nested_scope_cannot_clear_preexisting_failed_link():
    storage = MemoryGraphStorage()
    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            storage.create_node("Issue", "issue-1", {})
            claim(storage)
            del storage._nodes[("Issue", "issue-1")]
            with pytest.raises(StorageUnavailable):
                storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
            with pytest.raises(StorageUnavailable, match="nested transaction failed"):
                with storage.transaction():
                    storage.create_node("Issue", "other", {})
    assert not storage.query() and not storage.list_edges()


def test_memory_consumed_proof_is_restored_when_its_nested_link_rolls_back():
    storage = MemoryGraphStorage()
    with storage.transaction():
        storage.create_node("Issue", "issue-1", {})
        claim(storage)
        with pytest.raises(RuntimeError):
            with storage.transaction():
                storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
                raise RuntimeError("rollback nested link")
        assert not storage.list_edges()
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert len(storage.list_edges()) == 1


def receipt_row(*, created=True, element="fixture-element"):
    return {
        "labels": ["EventReceipt"],
        "id": "receipt-1",
        "archived": False,
        "properties": {
            "id": "receipt-1",
            "archived": False,
            **Neo4jGraphStorage._generic_properties(props()),
        },
        "created": created,
        "element_id": element,
    }


def edge_row():
    return {
        "from_labels": ["Issue"],
        "from_id": "issue-1",
        "relationship": "EMITTED_EVENT",
        "to_labels": ["EventReceipt"],
        "to_id": "receipt-1",
        "properties": {},
    }


def endpoint_row():
    return {
        "subject_labels": ["Issue"],
        "subject_id": "issue-1",
        "subject_element_id": "subject-element",
        "receipt_labels": ["EventReceipt"],
        "receipt_id": "receipt-1",
        "receipt_element_id": "fixture-element",
    }


def neo_fixture(
    monkeypatch, *, created=True, element="fixture-element", edges=None, endpoints=None, schema=None
):
    storage, transactions = storage_fixture()
    calls = []

    def run(query, **parameters):
        calls.append((query, parameters))
        if query.startswith("MERGE (n:`EventReceipt`"):
            return [receipt_row(created=created, element=element)]
        if query.startswith("SHOW CONSTRAINTS"):
            return (
                schema
                if schema is not None
                else [
                    {"type": "UNIQUENESS", "labelsOrTypes": [label], "properties": ["id"]}
                    for label in ("Issue", "EventReceipt")
                ]
            )
        if "AS subject_element_id" in query:
            return [endpoint_row()] if endpoints is None else endpoints
        return [edge_row()] if edges is None else edges

    monkeypatch.setattr(storage, "_run_graph", run)
    return storage, transactions, calls


def test_neo4j_creates_link_with_exact_physical_receipt_and_bounded_unique_endpoints(monkeypatch):
    storage, transactions, calls = neo_fixture(monkeypatch)
    with storage.transaction():
        claim(storage)
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert transactions[0].commits == 1
    query, values = calls[-1]
    assert "MERGE" not in query and "CREATE (source)-[edge:EMITTED_EVENT" in query
    lookup, lookup_values = calls[-2]
    assert "LIMIT 2" in lookup and "USING INDEX" in lookup
    assert "CREATE" not in lookup and "MERGE" not in lookup
    assert _work_query_phase(lookup) == "receipt_link_lookup"
    assert "elementId(target) = $receipt_element_id" in query
    assert "elementId(source) = $subject_element_id" in query
    assert "source.id = $subject_id" in query and "target.id = $receipt_id" in query
    assert "NOT (target)<-[:EMITTED_EVENT]-()" in query
    assert values["receipt_element_id"] == "fixture-element"
    assert values["subject_element_id"] == "subject-element"
    assert lookup_values == {"subject_id": "issue-1", "receipt_id": "receipt-1"}
    assert "receipt-1" not in query and "issue-1" not in query
    assert _work_query_phase(query) == "receipt_link"
    assert not hasattr(storage._transaction_state, "fresh_receipt_claims")
    with storage.transaction():
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")


@pytest.mark.parametrize("created", [False, True])
def test_neo4j_caller_cannot_link_without_current_transaction_claim(monkeypatch, created):
    storage, _, calls = neo_fixture(monkeypatch, created=created)
    if created:
        claim(storage)  # Auto-commit claim deliberately mints no transaction proof.
    with storage.transaction():
        if not created:
            claim(storage)
        count = len(calls)
        with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
        assert len(calls) == count


@pytest.mark.parametrize(
    "edges", [[], [edge_row(), edge_row()], [{**edge_row(), "to_id": "other"}]]
)
def test_neo4j_bad_result_poisons_outer_commit_even_if_caller_catches_error(monkeypatch, edges):
    storage, transactions, _ = neo_fixture(monkeypatch, edges=edges)
    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            claim(storage)
            with pytest.raises(StorageUnavailable):
                storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert transactions[0].commits == 0 and transactions[0].rollbacks == 1
    assert not hasattr(storage._transaction_state, "fresh_receipt_claims")


def test_neo4j_missing_physical_identity_cannot_mint_proof(monkeypatch):
    storage, transactions, _ = neo_fixture(monkeypatch, element=None)
    with pytest.raises(StorageUnavailable, match="invalid fresh receipt claim"):
        with storage.transaction():
            claim(storage)
    assert transactions[0].rollbacks == 1


def test_neo4j_proof_cannot_cross_worker_transactions(monkeypatch):
    storage, _, _ = neo_fixture(monkeypatch)
    with storage.transaction():
        claim(storage)

        def foreign():
            with storage.transaction():
                with pytest.raises(StorageUnavailable, match="fresh receipt claim required"):
                    storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(foreign).result(timeout=5)
        storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")


def test_neo4j_query_failure_is_atomic_even_if_caught(monkeypatch):
    storage, transactions, _ = neo_fixture(monkeypatch)
    original = storage._run_graph

    def fail(query, **parameters):
        if _work_query_phase(query) == "receipt_link":
            raise StorageUnavailable("synthetic edge write failure")
        return original(query, **parameters)

    monkeypatch.setattr(storage, "_run_graph", fail)
    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            claim(storage)
            with pytest.raises(StorageUnavailable, match="synthetic edge write failure"):
                storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert transactions[0].commits == 0 and transactions[0].rollbacks == 1
    assert not hasattr(storage._transaction_state, "fresh_receipt_claims")


@pytest.mark.parametrize(
    "schema",
    [
        [],
        [{"type": "UNIQUENESS", "labelsOrTypes": ["Issue"], "properties": ["id", "other"]}],
        [{"type": "UNIQUENESS", "labelsOrTypes": ["Issue", "Other"], "properties": ["id"]}],
        [{"type": "UNIQUENESS", "labelsOrTypes": ["Other"], "properties": ["id"]}],
        [{"type": "NODE_PROPERTY_EXISTENCE", "labelsOrTypes": ["Issue"], "properties": ["id"]}],
    ],
)
def test_neo4j_missing_or_misleading_constraints_prevent_link(monkeypatch, schema):
    storage, transactions, calls = neo_fixture(monkeypatch, schema=schema)
    with pytest.raises(StorageUnavailable, match="receipt endpoint schema unavailable"):
        with storage.transaction():
            claim(storage)
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert not any(_work_query_phase(query) == "receipt_link" for query, _ in calls)
    assert transactions[0].rollbacks == 1


@pytest.mark.parametrize(
    "endpoints",
    [
        [],
        [endpoint_row(), endpoint_row()],
        [{**endpoint_row(), "subject_labels": ["Issue", "Other"]}],
        [{**endpoint_row(), "subject_id": "other"}],
        [{**endpoint_row(), "subject_element_id": None}],
        [{**endpoint_row(), "receipt_element_id": "replacement-element"}],
    ],
)
def test_neo4j_ambiguous_or_changed_lookup_prevents_create(monkeypatch, endpoints):
    storage, transactions, calls = neo_fixture(monkeypatch, endpoints=endpoints)
    with pytest.raises(StorageUnavailable):
        with storage.transaction():
            claim(storage)
            storage.link_new_event_receipt("Issue", "issue-1", "receipt-1")
    assert not any(_work_query_phase(query) == "receipt_link" for query, _ in calls)
    assert transactions[0].rollbacks == 1
