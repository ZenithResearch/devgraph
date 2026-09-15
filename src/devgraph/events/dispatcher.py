"""Deterministic local dry-run dispatcher for EventReceipt outbox rows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from devgraph.events.model import EventReceipt, OutboxStatus
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import GraphStorage


class DryRunEventDispatcher:
    """Processes due receipts without external I/O or delivery adapters."""

    def __init__(
        self,
        storage: GraphStorage,
        *,
        fail_receipt: Callable[[EventReceipt], bool] | None = None,
        initial_backoff_seconds: int = 60,
        max_attempts: int = 3,
    ) -> None:
        self._storage = storage
        self._fail_receipt = fail_receipt or (lambda receipt: False)
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_attempts = max_attempts

    def dispatch_due(self, *, now: datetime) -> list[EventReceipt]:
        attempted: list[EventReceipt] = []
        with self._storage.transaction():
            for receipt in self._eligible_receipts(now):
                attempted.append(self._dispatch_one(receipt, now))
        return attempted

    def _eligible_receipts(self, now: datetime) -> list[EventReceipt]:
        receipts: list[EventReceipt] = []
        for node in self._storage.query(EVENT_RECEIPT_LABEL, archived=False):
            receipt = EventReceipt.from_node_properties(node.id, node.properties)
            if receipt.status not in {
                OutboxStatus.PENDING,
                OutboxStatus.RETRY_SCHEDULED,
            }:
                continue
            if receipt.next_attempt_at is not None and receipt.next_attempt_at > now:
                continue
            receipts.append(receipt)
        return receipts

    def _dispatch_one(self, receipt: EventReceipt, now: datetime) -> EventReceipt:
        attempt_count = receipt.attempt_count + 1
        if self._fail_receipt(receipt):
            if attempt_count >= self._max_attempts:
                updated = replace(
                    receipt,
                    status=OutboxStatus.FAILED,
                    attempt_count=attempt_count,
                    next_attempt_at=None,
                    updated_at=now,
                    last_error_summary=redact_text(
                        "dry-run dispatch failed; retry budget exhausted"
                    ),
                )
            else:
                updated = replace(
                    receipt,
                    status=OutboxStatus.RETRY_SCHEDULED,
                    attempt_count=attempt_count,
                    next_attempt_at=now + self._backoff_delay(attempt_count),
                    updated_at=now,
                    last_error_summary=redact_text("dry-run dispatch failed; retry scheduled"),
                )
        else:
            updated = replace(
                receipt,
                status=OutboxStatus.DISPATCHED_DRY_RUN,
                attempt_count=attempt_count,
                next_attempt_at=None,
                updated_at=now,
                last_error_summary="",
            )
        self._storage.update_node(
            EVENT_RECEIPT_LABEL,
            receipt.id,
            updated.to_node_properties(),
        )
        return updated

    def _backoff_delay(self, attempt_count: int) -> timedelta:
        return timedelta(seconds=self._initial_backoff_seconds * (2 ** (attempt_count - 1)))
