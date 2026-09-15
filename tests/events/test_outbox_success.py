from __future__ import annotations

from datetime import datetime, timedelta, timezone

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.model import OutboxStatus
from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL, EventOutbox
from devgraph.storage.memory import MemoryGraphStorage

RAW_IDEMPOTENCY_KEY = "synthetic-raw-idempotency-key-secret"


def _authority() -> AuthorityContext:
    return AuthorityContext(
        envelope=CredentialEnvelope(
            actor_id="actor-123",
            session_id="session-456",
            correlation_id="correlation-789",
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            issuer="issuer",
            audience="devgraph",
        )
    )


def test_successful_mutation_creates_exactly_one_pending_receipt_and_emitted_event_edge() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    _, receipt = outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={
            "message": "accepted proposal with secret=synthetic-credential-marker",
            "credential": "synthetic-credential-marker",
            "safe_count": 1,
        },
        mutation=lambda tx: tx.create_node("Issue", "issue-1", {"title": "Synthetic issue"}),
    )

    receipts = storage.query(EVENT_RECEIPT_LABEL)
    edges = storage.list_edges(EMITTED_EVENT)
    assert len(receipts) == 1
    assert len(edges) == 1

    stored = receipts[0]
    assert stored.id == receipt.id == "receipt-1"
    assert edges[0].from_label == "Issue"
    assert edges[0].from_id == "issue-1"
    assert edges[0].to_label == EVENT_RECEIPT_LABEL
    assert edges[0].to_id == "receipt-1"

    assert stored.properties["operation"] == "convert_accepted_proposal_to_issue"
    assert stored.properties["subject_label"] == "Issue"
    assert stored.properties["subject_id"] == "issue-1"
    assert stored.properties["actor_id"] == "actor-123"
    assert stored.properties["session_id"] == "session-456"
    assert stored.properties["correlation_id"] == "correlation-789"
    assert stored.properties["status"] == OutboxStatus.PENDING.value
    assert stored.properties["attempt_count"] == 0
    assert stored.properties["next_attempt_at"] is None
    assert stored.properties["idempotency_claim_digest"]
    assert "idempotency_key_digest" not in stored.properties
    assert RAW_IDEMPOTENCY_KEY not in repr(stored.properties)
    assert "synthetic-credential-marker" not in repr(stored.properties["redacted_summary"])
    assert stored.properties["redacted_summary"] == {
        "message": "accepted proposal with [REDACTED]",
        "credential": "[REDACTED]",
        "safe_count": 1,
    }
