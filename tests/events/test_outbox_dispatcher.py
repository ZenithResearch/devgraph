from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.dispatcher import DryRunEventDispatcher
from devgraph.events.model import EventReceipt, OutboxStatus
from devgraph.events.outbox import EVENT_RECEIPT_LABEL, EventOutbox
from devgraph.storage.memory import MemoryGraphStorage

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _authority() -> AuthorityContext:
    return AuthorityContext(
        envelope=CredentialEnvelope(
            actor_id="actor-1",
            session_id="session-1",
            correlation_id="correlation-1",
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=NOW + timedelta(hours=1),
            issuer="issuer",
            audience="devgraph",
        )
    )


def _storage_with_receipt(receipt_id: str = "receipt-1") -> MemoryGraphStorage:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: receipt_id)
    outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=f"synthetic-key-{receipt_id}",
        summary={"message": "created issue"},
        mutation=lambda tx: tx.create_node("Issue", "issue-1", {"title": "Synthetic issue"}),
    )
    return storage


def _receipt_properties(storage: MemoryGraphStorage, receipt_id: str = "receipt-1"):
    node = storage.get_node(EVENT_RECEIPT_LABEL, receipt_id)
    assert node is not None
    return node.properties


def test_pending_receipt_becomes_dispatched_dry_run_and_attempt_count_increments() -> None:
    storage = _storage_with_receipt()
    dispatcher = DryRunEventDispatcher(storage)

    dispatched = dispatcher.dispatch_due(now=NOW)

    assert [receipt.id for receipt in dispatched] == ["receipt-1"]
    properties = _receipt_properties(storage)
    assert properties["status"] == OutboxStatus.DISPATCHED_DRY_RUN.value
    assert properties["attempt_count"] == 1
    assert properties["updated_at"] == NOW.isoformat()


def test_simulated_failure_schedules_retry_with_deterministic_backoff() -> None:
    storage = _storage_with_receipt()
    dispatcher = DryRunEventDispatcher(
        storage,
        fail_receipt=lambda receipt: receipt.id == "receipt-1",
        initial_backoff_seconds=30,
        max_attempts=3,
    )

    attempted = dispatcher.dispatch_due(now=NOW)

    assert [receipt.id for receipt in attempted] == ["receipt-1"]
    properties = _receipt_properties(storage)
    assert properties["status"] == OutboxStatus.RETRY_SCHEDULED.value
    assert properties["attempt_count"] == 1
    assert properties["next_attempt_at"] == (NOW + timedelta(seconds=30)).isoformat()
    assert "dry-run dispatch failed" in properties["last_error_summary"]


def test_exhausted_retry_budget_becomes_failed() -> None:
    storage = _storage_with_receipt()
    storage.update_node(
        EVENT_RECEIPT_LABEL,
        "receipt-1",
        {
            "status": OutboxStatus.RETRY_SCHEDULED.value,
            "attempt_count": 2,
            "next_attempt_at": NOW.isoformat(),
        },
    )
    dispatcher = DryRunEventDispatcher(
        storage,
        fail_receipt=lambda receipt: True,
        initial_backoff_seconds=30,
        max_attempts=3,
    )

    dispatcher.dispatch_due(now=NOW)

    properties = _receipt_properties(storage)
    assert properties["status"] == OutboxStatus.FAILED.value
    assert properties["attempt_count"] == 3
    assert properties["next_attempt_at"] is None


def test_future_next_attempt_at_is_skipped() -> None:
    storage = _storage_with_receipt()
    storage.update_node(
        EVENT_RECEIPT_LABEL,
        "receipt-1",
        {
            "status": OutboxStatus.RETRY_SCHEDULED.value,
            "attempt_count": 1,
            "next_attempt_at": (NOW + timedelta(seconds=1)).isoformat(),
        },
    )
    dispatcher = DryRunEventDispatcher(storage)

    assert dispatcher.dispatch_due(now=NOW) == []

    properties = _receipt_properties(storage)
    assert properties["status"] == OutboxStatus.RETRY_SCHEDULED.value
    assert properties["attempt_count"] == 1


def test_dispatcher_does_not_mutate_work_records_beyond_receipt_status_fields() -> None:
    storage = _storage_with_receipt()
    before_issue = storage.get_node("Issue", "issue-1")
    assert before_issue is not None

    DryRunEventDispatcher(storage).dispatch_due(now=NOW)

    assert storage.get_node("Issue", "issue-1") == before_issue


def test_dispatcher_preserves_legacy_digest_field_without_claim_rewrite() -> None:
    storage = MemoryGraphStorage()
    legacy_digest = hashlib.sha256(b"legacy-key").hexdigest()
    legacy = EventReceipt(
        id="receipt-legacy",
        operation="create_issue",
        subject_label="Issue",
        subject_id="issue-legacy",
        actor_id="actor-legacy",
        session_id="session-legacy",
        correlation_id="correlation-legacy",
        idempotency_claim_digest=None,
        idempotency_key_digest=legacy_digest,
        created_at=NOW,
        updated_at=NOW,
    )
    storage.create_node(EVENT_RECEIPT_LABEL, legacy.id, legacy.to_node_properties())

    DryRunEventDispatcher(storage).dispatch_due(now=NOW)

    properties = _receipt_properties(storage, legacy.id)
    assert properties["idempotency_key_digest"] == legacy_digest
    assert "idempotency_claim_digest" not in properties
    assert properties["status"] == OutboxStatus.DISPATCHED_DRY_RUN.value
