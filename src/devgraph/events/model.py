"""Event receipt model and storage serialization helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from devgraph.policy.redaction import redact_event

_EXACT_ISSUE_CREATE_OPERATION = "devgraph.issue.create.v1"
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class OutboxStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED_DRY_RUN = "dispatched_dry_run"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EventReceipt:
    id: str
    operation: str
    subject_label: str
    subject_id: str
    actor_id: str
    session_id: str
    correlation_id: str
    idempotency_key_digest: str | None = None
    idempotency_claim_digest: str | None = None
    request_digest_sha256: str | None = None
    idempotency_key_digest_sha256: str | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    redacted_summary: dict[str, Any] = field(default_factory=dict)
    last_error_summary: str = ""

    def __post_init__(self) -> None:
        if (self.idempotency_claim_digest is None) == (
            self.idempotency_key_digest is None
        ):
            raise ValueError("receipt requires exactly one idempotency digest")
        exact_digests = (
            self.request_digest_sha256,
            self.idempotency_key_digest_sha256,
        )
        if self.operation == _EXACT_ISSUE_CREATE_OPERATION and any(
            not isinstance(value, str) or _LOWER_HEX_64.fullmatch(value) is None
            for value in exact_digests
        ):
            raise ValueError("exact Issue-create receipt requires exact digests")

    @classmethod
    def new(
        cls,
        *,
        receipt_id: str,
        operation: str,
        subject_label: str,
        subject_id: str,
        actor_id: str,
        session_id: str,
        correlation_id: str,
        idempotency_claim_digest: str,
        summary: Mapping[str, Any],
        request_digest_sha256: str | None = None,
        idempotency_key_digest_sha256: str | None = None,
        now: datetime | None = None,
    ) -> EventReceipt:
        timestamp = now or utc_now()
        return cls(
            id=receipt_id,
            operation=operation,
            subject_label=subject_label,
            subject_id=subject_id,
            actor_id=actor_id,
            session_id=session_id,
            correlation_id=correlation_id,
            idempotency_claim_digest=idempotency_claim_digest,
            request_digest_sha256=request_digest_sha256,
            idempotency_key_digest_sha256=idempotency_key_digest_sha256,
            status=OutboxStatus.PENDING,
            attempt_count=0,
            next_attempt_at=None,
            created_at=timestamp,
            updated_at=timestamp,
            redacted_summary=redact_event(summary),
            last_error_summary="",
        )

    def to_node_properties(self) -> dict[str, Any]:
        properties = {
            "operation": self.operation,
            "subject_label": self.subject_label,
            "subject_id": self.subject_id,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "next_attempt_at": _datetime_to_storage(self.next_attempt_at),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "redacted_summary": dict(self.redacted_summary),
            "last_error_summary": self.last_error_summary,
        }
        if self.idempotency_claim_digest is not None:
            properties["idempotency_claim_digest"] = self.idempotency_claim_digest
        if self.idempotency_key_digest is not None:
            properties["idempotency_key_digest"] = self.idempotency_key_digest
        if self.request_digest_sha256 is not None:
            properties["request_digest_sha256"] = self.request_digest_sha256
        if self.idempotency_key_digest_sha256 is not None:
            properties["idempotency_key_digest_sha256"] = (
                self.idempotency_key_digest_sha256
            )
        return properties

    @classmethod
    def from_node_properties(cls, receipt_id: str, properties: Mapping[str, Any]) -> EventReceipt:
        return cls(
            id=receipt_id,
            operation=str(properties["operation"]),
            subject_label=str(properties["subject_label"]),
            subject_id=str(properties["subject_id"]),
            actor_id=str(properties["actor_id"]),
            session_id=str(properties["session_id"]),
            correlation_id=str(properties["correlation_id"]),
            idempotency_claim_digest=(
                None
                if properties.get("idempotency_claim_digest") is None
                else str(properties["idempotency_claim_digest"])
            ),
            idempotency_key_digest=(
                None
                if properties.get("idempotency_key_digest") is None
                else str(properties["idempotency_key_digest"])
            ),
            request_digest_sha256=(
                None
                if properties.get("request_digest_sha256") is None
                else str(properties["request_digest_sha256"])
            ),
            idempotency_key_digest_sha256=(
                None
                if properties.get("idempotency_key_digest_sha256") is None
                else str(properties["idempotency_key_digest_sha256"])
            ),
            status=OutboxStatus(str(properties["status"])),
            attempt_count=int(properties["attempt_count"]),
            next_attempt_at=_datetime_from_storage(properties.get("next_attempt_at")),
            created_at=_datetime_from_storage(properties["created_at"]),
            updated_at=_datetime_from_storage(properties["updated_at"]),
            redacted_summary=dict(properties.get("redacted_summary", {})),
            last_error_summary=str(properties.get("last_error_summary", "")),
        )


def _datetime_to_storage(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _datetime_from_storage(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
