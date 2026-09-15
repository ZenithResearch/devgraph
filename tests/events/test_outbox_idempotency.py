from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread

import pytest

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.model import EventReceipt
from devgraph.events.outbox import (
    EMITTED_EVENT,
    EVENT_RECEIPT_LABEL,
    EventOutbox,
    IdempotencyScopeConflict,
    canonical_idempotency_claim_bytes,
    digest_idempotency_claim,
)
from devgraph.storage.memory import MemoryGraphStorage

RAW_IDEMPOTENCY_KEY = "synthetic-raw-idempotency-key-secret"


def _authority(
    *,
    actor_id: str = "actor-1",
    session_id: str = "session-1",
    correlation_id: str = "correlation-1",
    issuer: str = "issuer",
    audience: str = "devgraph",
) -> AuthorityContext:
    return AuthorityContext(
        envelope=CredentialEnvelope(
            actor_id=actor_id,
            session_id=session_id,
            correlation_id=correlation_id,
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            issuer=issuer,
            audience=audience,
        )
    )


def _record_subject(storage: MemoryGraphStorage, subject_id: str = "issue-1") -> str:
    storage.create_node("Issue", subject_id, {"title": f"Synthetic {subject_id}"})
    return f"created-{subject_id}"


def test_duplicate_same_operation_subject_digest_returns_existing_receipt_without_mutation(
) -> None:
    storage = MemoryGraphStorage()
    ids = iter(["receipt-1", "receipt-2"])
    outbox = EventOutbox(storage, receipt_id_factory=lambda: next(ids))
    mutation_calls = 0

    def mutation(tx: MemoryGraphStorage) -> str:
        nonlocal mutation_calls
        mutation_calls += 1
        return _record_subject(tx)

    first_result, first_receipt = outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={"message": "created issue"},
        mutation=mutation,
    )
    second_result, second_receipt = outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={"message": "created issue again"},
        mutation=lambda tx: pytest.fail("duplicate idempotent call reran mutation"),
    )

    assert first_result == "created-issue-1"
    assert second_result is None
    assert second_receipt == first_receipt
    assert mutation_calls == 1
    assert [node.id for node in storage.query(EVENT_RECEIPT_LABEL)] == ["receipt-1"]
    assert len(storage.list_edges(EMITTED_EVENT)) == 1


def test_same_digest_with_different_operation_fails_closed() -> None:
    storage = MemoryGraphStorage()
    ids = iter(["receipt-1", "receipt-2"])
    outbox = EventOutbox(storage, receipt_id_factory=lambda: next(ids))
    outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={"message": "created issue"},
        mutation=lambda tx: _record_subject(tx),
    )

    with pytest.raises(IdempotencyScopeConflict, match="idempotency key digest already exists"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="archive_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key=RAW_IDEMPOTENCY_KEY,
            summary={"message": "archive issue"},
            mutation=lambda tx: pytest.fail("conflicting scope reran mutation"),
        )

    assert [node.id for node in storage.query(EVENT_RECEIPT_LABEL)] == ["receipt-1"]


def test_same_digest_with_different_subject_fails_closed() -> None:
    storage = MemoryGraphStorage()
    ids = iter(["receipt-1", "receipt-2"])
    outbox = EventOutbox(storage, receipt_id_factory=lambda: next(ids))
    outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={"message": "created issue"},
        mutation=lambda tx: _record_subject(tx, "issue-1"),
    )

    with pytest.raises(IdempotencyScopeConflict, match="idempotency key digest already exists"):
        outbox.record_mutation_with_receipt(
            authority=_authority(),
            operation="convert_accepted_proposal_to_issue",
            subject_label="Issue",
            subject_id="issue-2",
            idempotency_key=RAW_IDEMPOTENCY_KEY,
            summary={"message": "created issue 2"},
            mutation=lambda tx: pytest.fail("conflicting subject reran mutation"),
        )

    assert storage.get_node("Issue", "issue-2") is None
    assert [node.id for node in storage.query(EVENT_RECEIPT_LABEL)] == ["receipt-1"]


def test_raw_idempotency_key_absent_from_stored_node_and_serialized_receipt() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")

    _, receipt = outbox.record_mutation_with_receipt(
        authority=_authority(),
        operation="convert_accepted_proposal_to_issue",
        subject_label="Issue",
        subject_id="issue-1",
        idempotency_key=RAW_IDEMPOTENCY_KEY,
        summary={"message": "created issue"},
        mutation=lambda tx: _record_subject(tx),
    )

    stored = storage.get_node(EVENT_RECEIPT_LABEL, "receipt-1")
    assert stored is not None
    assert RAW_IDEMPOTENCY_KEY not in repr(stored.properties)
    assert RAW_IDEMPOTENCY_KEY not in repr(receipt.to_node_properties())
    assert "idempotency_key" not in stored.properties
    assert "idempotency_key" not in EventReceipt.from_node_properties(
        "receipt-1", stored.properties
    ).to_node_properties()
    assert stored.properties["idempotency_claim_digest"]
    assert "idempotency_key_digest" not in stored.properties
    assert stored.properties["idempotency_claim_digest"] != hashlib.sha256(
        RAW_IDEMPOTENCY_KEY.encode("utf-8")
    ).hexdigest()


def test_principal_scoped_claim_has_fixed_bytes_and_digest_vector() -> None:
    authority = _authority(
        issuer="issuer-1",
        audience="devgraph",
        actor_id="actor-1",
        session_id="session-a",
        correlation_id="correlation-a",
    )

    assert canonical_idempotency_claim_bytes(authority, "retry-key").hex() == (
        "64657667726170682e6964656d706f74656e63792e636c61696d2e763100"
        "000000086973737565722d3100000008646576677261706800000007616374"
        "6f722d31000000097265747279"
        "2d6b6579"
    )
    assert digest_idempotency_claim(authority, "retry-key") == (
        "063f536907c2730aaae16c321a1b509713af5c0f5dc202ce34bfe593f871c5b3"
    )
    assert digest_idempotency_claim(_authority(), "key-1") == (
        "bd954fa21a00fc6a86e22709b9d22b00a1184c7e9346e4cd09838b763910c6ed"
    )


def test_claim_identity_ignores_session_but_changes_with_principal_domain() -> None:
    first = _authority(session_id="session-a", correlation_id="correlation-a")
    retry = _authority(session_id="session-b", correlation_id="correlation-b")
    baseline = digest_idempotency_claim(first, RAW_IDEMPOTENCY_KEY)

    assert digest_idempotency_claim(retry, RAW_IDEMPOTENCY_KEY) == baseline
    assert digest_idempotency_claim(
        _authority(actor_id="actor-2"), RAW_IDEMPOTENCY_KEY
    ) != baseline
    assert digest_idempotency_claim(
        _authority(issuer="issuer-2"), RAW_IDEMPOTENCY_KEY
    ) != baseline
    assert digest_idempotency_claim(
        _authority(audience="devgraph-other"), RAW_IDEMPOTENCY_KEY
    ) != baseline


def _concurrent_receipt_ids():
    lock = Lock()
    value = 0

    def next_id() -> str:
        nonlocal value
        with lock:
            value += 1
            return f"receipt-{value}"

    return next_id


def test_concurrent_same_principal_and_scope_has_one_winner() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=_concurrent_receipt_ids())
    start = Barrier(3)
    results: list[tuple[str | None, EventReceipt]] = []
    errors: list[BaseException] = []
    result_lock = Lock()
    mutation_calls = 0

    def worker(session_id: str) -> None:
        nonlocal mutation_calls
        try:
            start.wait()

            def mutation(tx: MemoryGraphStorage) -> str:
                nonlocal mutation_calls
                mutation_calls += 1
                return _record_subject(tx)

            result = outbox.record_mutation_with_receipt(
                authority=_authority(session_id=session_id),
                operation="create_issue",
                subject_label="Issue",
                subject_id="issue-1",
                idempotency_key=RAW_IDEMPOTENCY_KEY,
                summary={},
                mutation=mutation,
            )
            with result_lock:
                results.append(result)
        except BaseException as exc:
            with result_lock:
                errors.append(exc)

    threads = [Thread(target=worker, args=(f"session-{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert mutation_calls == 1
    assert sorted(result is None for result, _ in results) == [False, True]
    assert len({receipt.id for _, receipt in results}) == 1
    assert len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges(EMITTED_EVENT)) == 1


def test_concurrent_same_principal_different_scope_has_one_winner_and_one_conflict() -> None:
    storage = MemoryGraphStorage()
    outbox = EventOutbox(storage, receipt_id_factory=_concurrent_receipt_ids())
    start = Barrier(3)
    outcomes: list[str] = []
    result_lock = Lock()

    def worker(subject_id: str) -> None:
        try:
            start.wait()
            outbox.record_mutation_with_receipt(
                authority=_authority(),
                operation="create_issue",
                subject_label="Issue",
                subject_id=subject_id,
                idempotency_key=RAW_IDEMPOTENCY_KEY,
                summary={},
                mutation=lambda tx: _record_subject(tx, subject_id),
            )
            outcome = "created"
        except IdempotencyScopeConflict:
            outcome = "conflict"
        with result_lock:
            outcomes.append(outcome)

    threads = [Thread(target=worker, args=(subject_id,)) for subject_id in ("issue-1", "issue-2")]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["conflict", "created"]
    assert len(storage.query("Issue")) == 1
    assert len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges(EMITTED_EVENT)) == 1


def test_different_principals_sharing_raw_key_have_independent_claims() -> None:
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-shared", {"title": "Shared subject"})
    outbox = EventOutbox(storage, receipt_id_factory=_concurrent_receipt_ids())

    receipts = [
        outbox.record_mutation_with_receipt(
            authority=_authority(actor_id=actor_id, session_id=f"session-{actor_id}"),
            operation="update_issue",
            subject_label="Issue",
            subject_id="issue-shared",
            idempotency_key=RAW_IDEMPOTENCY_KEY,
            summary={},
            mutation=lambda tx, actor_id=actor_id: f"updated-{actor_id}",
        )[1]
        for actor_id in ("actor-1", "actor-2")
    ]

    assert {receipt.actor_id for receipt in receipts} == {"actor-1", "actor-2"}
    assert len({receipt.idempotency_claim_digest for receipt in receipts}) == 2
    assert len(storage.query(EVENT_RECEIPT_LABEL)) == 2
    assert len(storage.list_edges(EMITTED_EVENT)) == 2


@pytest.mark.parametrize("archived", [False, True])
def test_matching_legacy_raw_key_digest_fails_closed_without_receipt_disclosure(
    archived: bool,
) -> None:
    storage = MemoryGraphStorage()
    legacy = EventReceipt(
        id="receipt-legacy",
        operation="create_issue",
        subject_label="Issue",
        subject_id="issue-legacy",
        actor_id="legacy-actor",
        session_id="legacy-session",
        correlation_id="legacy-correlation",
        idempotency_claim_digest=None,
        idempotency_key_digest=hashlib.sha256(
            RAW_IDEMPOTENCY_KEY.encode("utf-8")
        ).hexdigest(),
    )
    storage.create_node(
        EVENT_RECEIPT_LABEL,
        legacy.id,
        legacy.to_node_properties(),
    )
    if archived:
        storage.archive_node(EVENT_RECEIPT_LABEL, legacy.id)
    outbox = EventOutbox(storage)

    with pytest.raises(IdempotencyScopeConflict, match="legacy idempotency claim"):
        outbox.record_mutation_with_receipt(
            authority=_authority(actor_id="different-actor"),
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-legacy",
            idempotency_key=RAW_IDEMPOTENCY_KEY,
            summary={},
            mutation=lambda tx: pytest.fail("legacy conflict reran mutation"),
        )

    assert storage.query(EVENT_RECEIPT_LABEL) == [
        storage.get_node(EVENT_RECEIPT_LABEL, "receipt-legacy")
    ]
