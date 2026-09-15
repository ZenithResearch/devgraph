from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL, EventOutbox
from devgraph.storage.memory import MemoryGraphStorage


def _authority() -> AuthorityContext:
    return AuthorityContext(
        envelope=CredentialEnvelope(
            actor_id="actor-1",
            session_id="session-1",
            correlation_id="correlation-1",
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            issuer="issuer",
            audience="devgraph",
        )
    )


def _record_issue(storage: MemoryGraphStorage, *, issue_id: str = "issue-1") -> str:
    storage.create_node("Issue", issue_id, {"title": "Synthetic issue"})
    return "mutation-result"


def _receipts(storage: MemoryGraphStorage):
    return storage.query(EVENT_RECEIPT_LABEL)


def _emitted_edges(storage: MemoryGraphStorage):
    return storage.list_edges(EMITTED_EVENT)


def test_success_persists_mutation_receipt_and_edge_in_one_transaction() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    result, receipt = outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key="synthetic-idempotency-key",
        summary={"message": "created issue"},
        mutation=lambda tx: _record_issue(tx),
    )

    assert result == "mutation-result"
    assert receipt.id == "receipt-1"
    assert storage.get_node("Issue", "issue-1") is not None
    assert [node.id for node in _receipts(storage)] == ["receipt-1"]
    assert _emitted_edges(storage) == [
        storage.list_edges(EMITTED_EVENT)[0]
    ]
    edge = _emitted_edges(storage)[0]
    assert (edge.from_label, edge.from_id, edge.to_label, edge.to_id) == (
        "Issue",
        "issue-1",
        EVENT_RECEIPT_LABEL,
        "receipt-1",
    )


def test_mutation_exception_leaves_no_receipt_or_event_edge() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    def failing_mutation(tx: MemoryGraphStorage) -> None:
        tx.create_node("Issue", "issue-1", {"title": "partial"})
        raise RuntimeError("synthetic mutation failure")

    with pytest.raises(RuntimeError, match="synthetic mutation failure"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key="synthetic-idempotency-key",
            summary={"message": "created issue"},
            mutation=failing_mutation,
        )

    assert storage.get_node("Issue", "issue-1") is None
    assert _receipts(storage) == []
    assert _emitted_edges(storage) == []


class ReceiptNodeFailingStorage(MemoryGraphStorage):
    def create_node(self, label, node_id, properties=None):
        if label == EVENT_RECEIPT_LABEL:
            raise RuntimeError("synthetic receipt node failure")
        return super().create_node(label, node_id, properties)


class ReceiptEdgeFailingStorage(MemoryGraphStorage):
    def create_edge(self, from_label, from_id, relationship, to_label, to_id, properties=None):
        if relationship == EMITTED_EVENT:
            raise RuntimeError("synthetic receipt edge failure")
        return super().create_edge(from_label, from_id, relationship, to_label, to_id, properties)


def test_mutation_failure_before_receipt_creation_leaves_no_partial_mutation() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    def failing_mutation(tx: MemoryGraphStorage) -> None:
        tx.create_node("Issue", "issue-1", {"title": "partial"})
        tx.create_node("Task", "task-1", {"title": "also partial"})
        tx.create_edge("Issue", "issue-1", "HAS_CHILD", "Task", "task-1")
        raise RuntimeError("synthetic graph mutation failure before receipt")

    with pytest.raises(RuntimeError, match="synthetic graph mutation failure before receipt"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key="synthetic-idempotency-key",
            summary={"message": "created issue"},
            mutation=failing_mutation,
        )

    assert storage.get_node("Issue", "issue-1") is None
    assert storage.get_node("Task", "task-1") is None
    assert storage.list_edges() == []
    assert _receipts(storage) == []
    assert _emitted_edges(storage) == []


def test_receipt_node_creation_failure_after_mutation_rolls_back_everything() -> None:
    storage = ReceiptNodeFailingStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    with pytest.raises(RuntimeError, match="synthetic receipt node failure"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key="synthetic-idempotency-key",
            summary={"message": "created issue"},
            mutation=lambda tx: _record_issue(tx),
        )

    assert storage.get_node("Issue", "issue-1") is None
    assert _receipts(storage) == []
    assert _emitted_edges(storage) == []


def test_receipt_edge_creation_failure_rolls_back_mutation() -> None:
    storage = ReceiptEdgeFailingStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    with pytest.raises(RuntimeError, match="synthetic receipt edge failure"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key="synthetic-idempotency-key",
            summary={"message": "created issue"},
            mutation=lambda tx: _record_issue(tx),
        )

    assert storage.get_node("Issue", "issue-1") is None
    assert _receipts(storage) == []
    assert _emitted_edges(storage) == []
