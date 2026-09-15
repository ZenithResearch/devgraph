"""Transactional local outbox for EventReceipt persistence."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from unicodedata import category as unicode_category

from devgraph.auth.context import AuthorityContext
from devgraph.events.model import EventReceipt, utc_now
from devgraph.storage.base import EventOutboxStorage, GraphStorage

EVENT_RECEIPT_LABEL = "EventReceipt"
EMITTED_EVENT = "EMITTED_EVENT"
IDEMPOTENCY_CLAIM_DOMAIN = b"devgraph.idempotency.claim.v1\x00"
DEVGRAPH_ISSUE_CREATE_OPERATION_V1 = "devgraph.issue.create.v1"
_AUTHORITY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_EXACT_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{16,128}$", re.ASCII)
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class IdempotencyScopeConflict(RuntimeError):
    """Raised when an idempotency digest is reused outside its pinned scope."""


class IdempotencyPrincipal(Protocol):
    """Narrow identity needed to scope a retry claim.

    This is deliberately not an authorization capability. Exact operation
    adapters may supply a private verified value without manufacturing a
    generic credential envelope.
    """

    @property
    def issuer(self) -> str: ...

    @property
    def audience(self) -> str: ...

    @property
    def actor_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def correlation_id(self) -> str: ...


@dataclass(frozen=True)
class _AuthorityPrincipal:
    issuer: str
    audience: str
    actor_id: str
    session_id: str
    correlation_id: str


class EventOutbox:
    def __init__(
        self,
        storage: EventOutboxStorage,
        *,
        receipt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._storage = storage
        self._receipt_id_factory = receipt_id_factory or (lambda: f"event-receipt-{uuid.uuid4()}")

    def record_mutation_with_receipt(
        self,
        *,
        authority: AuthorityContext,
        operation: str,
        subject_label: str,
        subject_id: str,
        idempotency_key: str,
        summary: Mapping[str, Any],
        mutation: Callable[[GraphStorage], Any],
    ) -> tuple[Any, EventReceipt]:
        return self._record_mutation_with_receipt(
            principal=_principal_from_authority(authority),
            operation=operation,
            subject_label=subject_label,
            subject_id=subject_id,
            idempotency_key=idempotency_key,
            summary=summary,
            mutation=mutation,
            request_digest_sha256=None,
            idempotency_key_digest_sha256=None,
            on_result=None,
        )

    def record_secs_issue_create_v1_with_receipt(
        self,
        *,
        principal: IdempotencyPrincipal,
        subject_id: str,
        idempotency_key: str,
        request_digest_sha256: str,
        idempotency_key_digest_sha256: str,
        summary: Mapping[str, Any],
        mutation: Callable[[GraphStorage], Any],
        on_result: Callable[[EventReceipt, bool], None],
    ) -> tuple[Any, EventReceipt]:
        """Record exactly one verified ``devgraph.issue.create.v1`` mutation."""

        if (
            not isinstance(idempotency_key, str)
            or _EXACT_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
            or not isinstance(request_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(request_digest_sha256) is None
            or not isinstance(idempotency_key_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(idempotency_key_digest_sha256) is None
        ):
            raise IdempotencyScopeConflict("invalid idempotency key")
        actual_key_digest = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
        if actual_key_digest != idempotency_key_digest_sha256:
            raise IdempotencyScopeConflict("idempotency key digest mismatch")
        return self._record_mutation_with_receipt(
            principal=principal,
            operation=DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
            subject_label="Issue",
            subject_id=subject_id,
            idempotency_key=idempotency_key,
            summary=summary,
            mutation=mutation,
            request_digest_sha256=request_digest_sha256,
            idempotency_key_digest_sha256=idempotency_key_digest_sha256,
            on_result=on_result,
        )

    def record_named_work_v1_with_receipt(
        self,
        *,
        principal: IdempotencyPrincipal,
        operation: str,
        subject_label: str,
        subject_id: str,
        idempotency_key: str,
        request_digest_sha256: str,
        idempotency_key_digest_sha256: str,
        summary: Mapping[str, Any],
        mutation: Callable[[GraphStorage], Any],
        on_result: Callable[[EventReceipt, bool], None],
    ) -> tuple[Any, EventReceipt]:
        from devgraph.arena_requests import ARENA_OPERATIONS
        from devgraph.work_requests import WORK_OPERATIONS

        valid_work = operation in {f"devgraph.work.{op}.v1" for op in WORK_OPERATIONS} and (
            subject_label in {"Proposal", "Initiative", "Project", "Issue", "Task"})
        valid_arena = operation in {f"devgraph.arena.{op}.v1" for op in ARENA_OPERATIONS} and (
            subject_label in ({"Initiative", "Task"} if operation == "devgraph.arena.member.set.v1"
                              else {"Arena"}))
        if not (valid_work or valid_arena):
            raise IdempotencyScopeConflict("invalid named Work operation")
        if (
            not isinstance(idempotency_key, str)
            or _EXACT_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise IdempotencyScopeConflict("invalid idempotency key")
        if (
            not isinstance(request_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(request_digest_sha256) is None
        ):
            raise IdempotencyScopeConflict("invalid request digest")
        if (
            hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
            != idempotency_key_digest_sha256
        ):
            raise IdempotencyScopeConflict("idempotency key digest mismatch")
        return self._record_mutation_with_receipt(
            principal=principal,
            operation=operation,
            subject_label=subject_label,
            subject_id=subject_id,
            idempotency_key=idempotency_key,
            summary=summary,
            mutation=mutation,
            request_digest_sha256=request_digest_sha256,
            idempotency_key_digest_sha256=idempotency_key_digest_sha256,
            on_result=on_result,
        )

    def _record_mutation_with_receipt(
        self,
        *,
        principal: IdempotencyPrincipal,
        operation: str,
        subject_label: str,
        subject_id: str,
        idempotency_key: str,
        summary: Mapping[str, Any],
        mutation: Callable[[GraphStorage], Any],
        request_digest_sha256: str | None,
        idempotency_key_digest_sha256: str | None,
        on_result: Callable[[EventReceipt, bool], None] | None,
    ) -> tuple[Any, EventReceipt]:
        idempotency_claim_digest = digest_idempotency_claim(principal, idempotency_key)
        with self._storage.transaction():
            existing_receipt = self._find_receipt_by_idempotency_digest(
                "idempotency_claim_digest",
                idempotency_claim_digest,
            )
            if existing_receipt is not None:
                _require_matching_scope(
                    existing_receipt,
                    operation=operation,
                    subject_label=subject_label,
                    subject_id=subject_id,
                    request_digest_sha256=request_digest_sha256,
                )
                if on_result is not None:
                    on_result(existing_receipt, True)
                return None, existing_receipt
            legacy_receipt = self._find_receipt_by_idempotency_digest(
                "idempotency_key_digest",
                hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            )
            if legacy_receipt is not None:
                raise IdempotencyScopeConflict("legacy idempotency claim requires a new key")

            now = utc_now()
            receipt = EventReceipt.new(
                receipt_id=self._receipt_id_factory(),
                operation=operation,
                subject_label=subject_label,
                subject_id=subject_id,
                actor_id=principal.actor_id,
                session_id=principal.session_id,
                correlation_id=principal.correlation_id,
                idempotency_claim_digest=idempotency_claim_digest,
                summary=summary,
                request_digest_sha256=request_digest_sha256,
                idempotency_key_digest_sha256=idempotency_key_digest_sha256,
                now=now,
            )
            claim = self._storage.claim_event_receipt(
                receipt.id,
                idempotency_claim_digest,
                receipt.to_node_properties(),
            )
            if not claim.created:
                existing_receipt = EventReceipt.from_node_properties(
                    claim.receipt.id,
                    claim.receipt.properties,
                )
                _require_matching_scope(
                    existing_receipt,
                    operation=operation,
                    subject_label=subject_label,
                    subject_id=subject_id,
                    request_digest_sha256=request_digest_sha256,
                )
                if on_result is not None:
                    on_result(existing_receipt, True)
                return None, existing_receipt

            result = mutation(self._storage)
            self._storage.create_edge(
                subject_label,
                subject_id,
                EMITTED_EVENT,
                EVENT_RECEIPT_LABEL,
                receipt.id,
            )
            if on_result is not None:
                on_result(receipt, False)
            return result, receipt

    def _find_receipt_by_idempotency_digest(
        self,
        property_name: str,
        idempotency_digest: str,
    ) -> EventReceipt | None:
        for node in self._storage.query(EVENT_RECEIPT_LABEL):
            if node.properties.get(property_name) == idempotency_digest:
                return EventReceipt.from_node_properties(node.id, node.properties)
        return None


def canonical_idempotency_claim_bytes(
    authority: AuthorityContext | IdempotencyPrincipal,
    idempotency_key: str,
) -> bytes:
    """Encode the principal-scoped retry identity without ambiguity."""

    if not isinstance(idempotency_key, str):
        raise TypeError("idempotency key must be a string")
    principal = (
        _principal_from_authority(authority)
        if isinstance(authority, AuthorityContext)
        else authority
    )
    if any(
        not isinstance(value, str) or _AUTHORITY_IDENTIFIER.fullmatch(value) is None
        for value in (principal.issuer, principal.actor_id)
    ) or not _is_safe_audience(principal.audience):
        raise ValueError("invalid idempotency claim authority")
    authority_values = (principal.issuer, principal.audience, principal.actor_id)
    values = (*authority_values, idempotency_key)
    encoded = bytearray(IDEMPOTENCY_CLAIM_DOMAIN)
    for value in values:
        field = value.encode("utf-8")
        encoded.extend(len(field).to_bytes(4, "big"))
        encoded.extend(field)
    return bytes(encoded)


def digest_idempotency_claim(
    authority: AuthorityContext | IdempotencyPrincipal,
    idempotency_key: str,
) -> str:
    return hashlib.sha256(canonical_idempotency_claim_bytes(authority, idempotency_key)).hexdigest()


def _principal_from_authority(authority: AuthorityContext) -> IdempotencyPrincipal:
    if not isinstance(authority, AuthorityContext):
        raise TypeError("invalid authority context")
    return _AuthorityPrincipal(
        issuer=authority.envelope.issuer,
        audience=authority.envelope.audience,
        actor_id=authority.actor_id,
        session_id=authority.session_id,
        correlation_id=authority.correlation_id,
    )


def _is_safe_audience(value: object) -> bool:
    if not isinstance(value, str) or value.strip() == "":
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= 256 and not any(
        unicode_category(character) == "Cc" for character in value
    )


def _require_matching_scope(
    receipt: EventReceipt,
    *,
    operation: str,
    subject_label: str,
    subject_id: str,
    request_digest_sha256: str | None = None,
) -> None:
    if (
        receipt.operation,
        receipt.subject_label,
        receipt.subject_id,
    ) != (operation, subject_label, subject_id) or (
        request_digest_sha256 is not None and receipt.request_digest_sha256 != request_digest_sha256
    ):
        raise IdempotencyScopeConflict(
            "idempotency key digest already exists for a different operation or subject"
        )
