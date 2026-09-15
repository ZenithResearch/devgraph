from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from devgraph.events.model import EventReceipt, OutboxStatus


def _receipt() -> EventReceipt:
    return EventReceipt.new(
        receipt_id="receipt-1",
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        actor_id="actor-1",
        session_id="session-1",
        correlation_id="correlation-1",
        idempotency_claim_digest="digest-1",
        summary={
            "message": "accepted with secret=synthetic-credential-marker",
            "credential": "synthetic-credential-marker",
            "safe": "value",
        },
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_outbox_status_values_are_stable_storage_values() -> None:
    assert [status.value for status in OutboxStatus] == [
        "pending",
        "dispatched_dry_run",
        "retry_scheduled",
        "failed",
    ]


def test_event_receipt_serializes_to_storage_safe_values() -> None:
    receipt = _receipt()

    properties = receipt.to_node_properties()

    assert properties == {
        "operation": "convert_accepted_proposal_to_issue",
        "subject_label": "Issue",
        "subject_id": "issue-1",
        "actor_id": "actor-1",
        "session_id": "session-1",
        "correlation_id": "correlation-1",
        "idempotency_claim_digest": "digest-1",
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "redacted_summary": {
            "message": "accepted with [REDACTED]",
            "credential": "[REDACTED]",
            "safe": "value",
        },
        "last_error_summary": "",
    }
    assert EventReceipt.from_node_properties("receipt-1", properties) == receipt


def test_redacted_summary_has_no_synthetic_credential_marker() -> None:
    receipt = _receipt()

    assert "synthetic-credential-marker" not in repr(receipt.redacted_summary)
    assert "synthetic-credential-marker" not in repr(receipt.to_node_properties())


def test_raw_idempotency_key_is_not_a_model_field() -> None:
    field_names = {field.name for field in dataclasses.fields(EventReceipt)}

    assert "idempotency_key" not in field_names
    assert "idempotency_claim_digest" in field_names
    assert "idempotency_key_digest" in field_names


def test_legacy_key_digest_round_trips_without_becoming_a_claim_digest() -> None:
    properties = _receipt().to_node_properties()
    properties["idempotency_key_digest"] = properties.pop(
        "idempotency_claim_digest"
    )

    legacy = EventReceipt.from_node_properties("receipt-legacy", properties)

    assert legacy.idempotency_claim_digest is None
    assert legacy.idempotency_key_digest == "digest-1"
    assert legacy.to_node_properties() == properties


def test_legacy_constructor_keeps_original_positional_digest_slot() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)

    legacy = EventReceipt(
        "receipt-legacy",
        "legacy_operation",
        "Issue",
        "issue-legacy",
        "actor-legacy",
        "session-legacy",
        "correlation-legacy",
        "legacy-digest",
        created_at=timestamp,
        updated_at=timestamp,
    )

    assert legacy.idempotency_key_digest == "legacy-digest"
    assert legacy.idempotency_claim_digest is None


@pytest.mark.parametrize(
    ("legacy_digest", "claim_digest"),
    [(None, None), ("legacy-digest", "claim-digest")],
)
def test_receipt_requires_exactly_one_idempotency_digest(
    legacy_digest: str | None,
    claim_digest: str | None,
) -> None:
    with pytest.raises(ValueError, match="exactly one idempotency digest"):
        EventReceipt(
            id="receipt-invalid",
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-invalid",
            actor_id="actor-invalid",
            session_id="session-invalid",
            correlation_id="correlation-invalid",
            idempotency_key_digest=legacy_digest,
            idempotency_claim_digest=claim_digest,
        )


@pytest.mark.parametrize(
    ("request_digest", "key_digest"),
    [
        (None, None),
        ("a" * 64, None),
        (None, "b" * 64),
        ("A" * 64, "b" * 64),
    ],
)
def test_exact_issue_create_receipt_requires_both_lowercase_sha256_digests(
    request_digest: str | None,
    key_digest: str | None,
) -> None:
    with pytest.raises(ValueError, match="requires exact digests"):
        EventReceipt.new(
            receipt_id="receipt-exact",
            operation="devgraph.issue.create.v1",
            subject_label="Issue",
            subject_id="issue-exact",
            actor_id="actor-exact",
            session_id="session-exact",
            correlation_id="correlation-exact",
            idempotency_claim_digest="claim-exact",
            request_digest_sha256=request_digest,
            idempotency_key_digest_sha256=key_digest,
            summary={},
        )
