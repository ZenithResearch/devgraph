from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from itertools import count
from pathlib import Path
from threading import Barrier, Event, Lock, Thread

import pytest
from fastapi.testclient import TestClient

from devgraph.api import ApiServices, create_app
from devgraph.auth import AuditLog, AuthorizedWorkGraph, CredentialEnvelope
from devgraph.auth.context import AuthorityContext
from devgraph.auth.errors import ForbiddenError
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL, EventOutbox
from devgraph.model.base import utc_now
from devgraph.model.initiative_observations import InitiativeObservationRepository
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph-authority-integrity"
CREDENTIAL = "synthetic-authority-integrity-credential"


def observation_body(observation_id: str) -> dict[str, object]:
    return {
        "id": observation_id,
        "project_id": "external-project-authority",
        "subject_kind": "github_repository",
        "subject_url": "https://github.com/ZenithResearch/devgraph",
        "github_node_id": "R_authority_integrity",
        "source_commit": "abcdef123456",
        "title": "Authority-bound observation",
        "problem": "Split authority would corrupt receipt attribution.",
        "desired_state": "One graph-owned session controls the mutation.",
        "evidence_urls": ["https://github.com/ZenithResearch/devgraph"],
        "confidence": 0.9,
        "observed_by": "synthetic-scout-key",
    }


def envelope(
    identity: str,
    scopes: frozenset[str] = frozenset({SCOPE_WRITE}),
) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id=f"actor-{identity}",
        session_id=f"session-{identity}",
        correlation_id=f"correlation-{identity}",
        scopes=scopes,
        expires_at=utc_now() + timedelta(hours=1),
        issuer="synthetic-authority-issuer",
        audience=AUDIENCE,
    )


@dataclass
class SequencedVerifier:
    contexts: tuple[AuthorityContext, ...]
    calls: int = 0

    def verify(self, credential, *, audience, now=None) -> AuthorityContext:
        assert credential == CREDENTIAL
        assert audience == AUDIENCE
        index = min(self.calls, len(self.contexts) - 1)
        self.calls += 1
        return self.contexts[index]


def build_client() -> tuple[TestClient, SequencedVerifier, AuditLog]:
    storage = MemoryGraphStorage()
    audit_log = AuditLog()
    verifier = SequencedVerifier(
        contexts=(
            AuthorityContext(envelope("first")),
            AuthorityContext(envelope("second")),
        )
    )
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
        repository=WorkObjectRepository(storage),
        initiative_observations=InitiativeObservationRepository(storage),
        monitor_storage=storage,
    )
    receipt_ids = count(1)
    services = ApiServices(
        authorized_graph=graph,
        outbox=EventOutbox(
            storage,
            receipt_id_factory=lambda: f"receipt-authority-{next(receipt_ids)}",
        ),
        storage=storage,
        verifier=verifier,
        audience=AUDIENCE,
    )
    return TestClient(create_app(services)), verifier, audit_log


def test_write_verifies_once_and_binds_receipt_and_audit_to_same_context() -> None:
    client, verifier, audit_log = build_client()

    response = client.post(
        "/work/Issue",
        headers={
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": "authority-integrity-key",
        },
        json={"id": "issue-authority-one", "title": "Synthetic authority proof"},
    )

    assert response.status_code == 201
    assert verifier.calls == 1
    assert response.json()["receipt"]["correlation_id"] == "correlation-first"
    assert len(audit_log.records) == 1
    audit = audit_log.records[0]
    assert (audit.actor_id, audit.session_id, audit.correlation_id) == (
        "actor-first",
        "session-first",
        "correlation-first",
    )


def test_observation_write_verifies_once_and_binds_receipt_and_audit_to_same_context() -> None:
    client, verifier, audit_log = build_client()

    response = client.post(
        "/initiative-observations",
        headers={
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": "observation-authority-integrity-key",
        },
        json=observation_body("observation-authority-one"),
    )

    assert response.status_code == 201
    assert verifier.calls == 1
    assert response.json()["receipt"]["correlation_id"] == "correlation-first"
    assert response.json()["observation"]["id"] == "observation-authority-one"
    assert len(audit_log.records) == 1
    audit = audit_log.records[0]
    assert (audit.actor_id, audit.session_id, audit.correlation_id) == (
        "actor-first",
        "session-first",
        "correlation-first",
    )


@pytest.mark.parametrize(
    "field", ["actor_id", "session_id", "correlation_id", "issuer", "audience"]
)
@pytest.mark.parametrize("value", ["line\nfeed", "carriage\rreturn", "control\x00value"])
def test_authority_identifiers_reject_control_characters(field: str, value: str) -> None:
    values = {
        "actor_id": "actor-safe",
        "session_id": "session-safe",
        "correlation_id": "correlation-safe",
        "scopes": frozenset({SCOPE_WRITE}),
        "expires_at": utc_now() + timedelta(hours=1),
        "issuer": "issuer-safe",
        "audience": AUDIENCE,
    }
    values[field] = value

    with pytest.raises(ValueError, match=f"invalid {field}"):
        CredentialEnvelope(**values)


class ReceiptNodeFailingStorage(MemoryGraphStorage):
    def create_node(self, label, node_id, properties=None):
        if label == EVENT_RECEIPT_LABEL:
            raise RuntimeError("synthetic receipt node failure")
        return super().create_node(label, node_id, properties)


class ReceiptEdgeFailingStorage(MemoryGraphStorage):
    def create_edge(self, from_label, from_id, relationship, to_label, to_id, properties=None):
        if relationship == EMITTED_EVENT:
            raise RuntimeError("synthetic receipt edge failure")
        return super().create_edge(
            from_label,
            from_id,
            relationship,
            to_label,
            to_id,
            properties,
        )


def build_failure_client(
    stage: str,
) -> tuple[TestClient, MemoryGraphStorage, AuditLog]:
    storage: MemoryGraphStorage
    if stage == "receipt_node":
        storage = ReceiptNodeFailingStorage()
    elif stage == "receipt_edge":
        storage = ReceiptEdgeFailingStorage()
    else:
        storage = MemoryGraphStorage()
    audit_log = AuditLog()
    storage.create_node("Issue", "issue-preexisting", {"title": "pre-existing"})
    audit_log.record(
        actor_id="actor-preexisting",
        session_id="session-preexisting",
        correlation_id="correlation-preexisting",
        category="write",
        operation="preexisting_operation",
    )
    verifier = SequencedVerifier(contexts=(AuthorityContext(envelope("atomic")),))
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
        repository=WorkObjectRepository(storage),
        initiative_observations=InitiativeObservationRepository(storage),
        monitor_storage=storage,
    )

    def receipt_id() -> str:
        if stage == "receipt_id":
            raise RuntimeError("synthetic receipt id failure")
        return "receipt-authority-atomic"

    services = ApiServices(
        authorized_graph=graph,
        outbox=EventOutbox(storage, receipt_id_factory=receipt_id),
        storage=storage,
        verifier=verifier,
        audience=AUDIENCE,
    )
    return (
        TestClient(create_app(services), raise_server_exceptions=False),
        storage,
        audit_log,
    )


def deep_snapshot(storage: MemoryGraphStorage, audit_log: AuditLog) -> tuple:
    return (
        deepcopy(storage.query()),
        deepcopy(storage.list_edges()),
        tuple(deepcopy(audit_log.records)),
    )


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        ("receipt_id", "synthetic receipt id failure"),
        ("receipt_node", "synthetic receipt node failure"),
        ("receipt_edge", "synthetic receipt edge failure"),
    ],
)
def test_post_mutation_failure_restores_work_receipt_edge_and_audit_snapshots(
    stage: str,
    message: str,
) -> None:
    client, storage, audit_log = build_failure_client(stage)
    before = deep_snapshot(storage, audit_log)
    safe_stage = stage.replace("_", "-")

    response = client.post(
        "/work/Issue",
        headers={
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": f"authority-atomic-{safe_stage}",
        },
        json={"id": f"issue-authority-{safe_stage}", "title": "Atomic audit proof"},
    )

    assert response.status_code == 500
    assert message not in response.text
    assert deep_snapshot(storage, audit_log) == before


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        ("receipt_id", "synthetic receipt id failure"),
        ("receipt_node", "synthetic receipt node failure"),
        ("receipt_edge", "synthetic receipt edge failure"),
    ],
)
def test_post_mutation_failure_restores_observation_receipt_edge_and_audit_snapshots(
    stage: str,
    message: str,
) -> None:
    client, storage, audit_log = build_failure_client(stage)
    before = deep_snapshot(storage, audit_log)
    safe_stage = stage.replace("_", "-")

    response = client.post(
        "/initiative-observations",
        headers={
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": f"observation-atomic-{safe_stage}",
        },
        json=observation_body(f"observation-authority-{safe_stage}"),
    )

    assert response.status_code == 500
    assert message not in response.text
    assert deep_snapshot(storage, audit_log) == before


def test_wrong_scope_duplicate_rejects_before_prior_receipt_disclosure() -> None:
    client, verifier, audit_log = build_client()
    verifier.contexts = (
        AuthorityContext(envelope("first")),
        AuthorityContext(envelope("read-only", scopes=frozenset())),
    )
    request = {
        "headers": {
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": "authority-duplicate-scope-key",
        },
        "json": {"id": "issue-authority-duplicate", "title": "Duplicate scope proof"},
    }

    first = client.post("/work/Issue", **request)
    denied = client.post("/work/Issue", **request)

    assert first.status_code == 201
    assert denied.status_code == 403
    assert "receipt" not in denied.text
    assert "correlation-first" not in denied.text
    assert len(audit_log.records) == 1


def test_wrong_scope_observation_duplicate_rejects_before_prior_receipt_disclosure() -> None:
    client, verifier, audit_log = build_client()
    verifier.contexts = (
        AuthorityContext(envelope("first")),
        AuthorityContext(envelope("read-only", scopes=frozenset())),
    )
    request = {
        "headers": {
            "Authorization": f"Bearer {CREDENTIAL}",
            "Idempotency-Key": "observation-authority-duplicate-key",
        },
        "json": observation_body("observation-authority-duplicate"),
    }

    first = client.post("/initiative-observations", **request)
    denied = client.post("/initiative-observations", **request)

    assert first.status_code == 201
    assert denied.status_code == 403
    assert "receipt" not in denied.text
    assert "correlation-first" not in denied.text
    assert len(audit_log.records) == 1


def test_concurrent_same_principal_retry_has_one_http_winner_and_one_duplicate() -> None:
    client, verifier, audit_log = build_client()
    same_context = AuthorityContext(envelope("same-principal"))
    verifier.contexts = (same_context, same_context)
    start = Barrier(3)
    responses = []
    response_lock = Lock()

    def send() -> None:
        start.wait(timeout=5)
        response = client.post(
            "/work/Issue",
            headers={
                "Authorization": f"Bearer {CREDENTIAL}",
                "Idempotency-Key": "concurrent-principal-key",
            },
            json={"id": "issue-concurrent-principal", "title": "One winner"},
        )
        with response_lock:
            responses.append(response)

    threads = [Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [200, 201]
    bodies = [response.json() for response in responses]
    assert sorted(body["receipt"]["duplicate"] for body in bodies) == [False, True]
    assert len({body["receipt"]["receipt_id"] for body in bodies}) == 1
    assert len(audit_log.records) == 1


def test_concurrent_same_principal_key_different_subject_has_one_safe_conflict() -> None:
    client, verifier, audit_log = build_client()
    verifier.contexts = (
        AuthorityContext(
            replace(
                envelope("scope-one"),
                actor_id="actor-shared-principal",
            )
        ),
        AuthorityContext(
            replace(
                envelope("scope-two"),
                actor_id="actor-shared-principal",
            )
        ),
    )
    start = Barrier(3)
    responses = []
    response_lock = Lock()

    def send(subject_id: str) -> None:
        start.wait(timeout=5)
        response = client.post(
            "/work/Issue",
            headers={
                "Authorization": f"Bearer {CREDENTIAL}",
                "Idempotency-Key": "concurrent-different-subject-key",
            },
            json={"id": subject_id, "title": f"Candidate {subject_id}"},
        )
        with response_lock:
            responses.append(response)

    threads = [
        Thread(target=send, args=(subject_id,))
        for subject_id in ("issue-scope-one", "issue-scope-two")
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert "receipt" not in conflict.text.lower()
    assert "correlation-scope-one" not in conflict.text
    assert "correlation-scope-two" not in conflict.text
    storage = client.app.state.services.storage
    assert isinstance(storage, MemoryGraphStorage)
    assert len(storage.query("Issue")) == 1
    assert len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges(EMITTED_EVENT)) == 1
    assert len(audit_log.records) == 1


def test_different_principals_sharing_raw_key_receive_only_their_own_receipts() -> None:
    client, verifier, audit_log = build_client()
    verifier.contexts = (
        AuthorityContext(envelope("principal-one")),
        AuthorityContext(envelope("principal-two")),
    )
    headers = {
        "Authorization": f"Bearer {CREDENTIAL}",
        "Idempotency-Key": "shared-raw-key-independent-principals",
    }

    first = client.post(
        "/work/Issue",
        headers=headers,
        json={"id": "issue-principal-one", "title": "Principal one"},
    )
    second = client.post(
        "/work/Issue",
        headers=headers,
        json={"id": "issue-principal-two", "title": "Principal two"},
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["receipt"]["correlation_id"] == "correlation-principal-one"
    assert second.json()["receipt"]["correlation_id"] == "correlation-principal-two"
    assert first.json()["receipt"]["receipt_id"] != second.json()["receipt"]["receipt_id"]
    assert [record.actor_id for record in audit_log.records] == [
        "actor-principal-one",
        "actor-principal-two",
    ]


def test_failed_audit_transaction_cannot_erase_concurrent_commit() -> None:
    audit_log = AuditLog()
    failed_recorded = Event()
    committed = Event()

    def fail_after_concurrent_commit() -> None:
        try:
            with audit_log.transaction():
                audit_log.record(
                    actor_id="actor-failed",
                    session_id="session-failed",
                    correlation_id="correlation-failed",
                    category="write",
                    operation="failed_operation",
                )
                failed_recorded.set()
                assert committed.wait(timeout=5)
                raise RuntimeError("synthetic failed transaction")
        except RuntimeError:
            pass

    def commit_concurrently() -> None:
        assert failed_recorded.wait(timeout=5)
        with audit_log.transaction():
            audit_log.record(
                actor_id="actor-committed",
                session_id="session-committed",
                correlation_id="correlation-committed",
                category="write",
                operation="committed_operation",
            )
        committed.set()

    failing_thread = Thread(target=fail_after_concurrent_commit)
    committed_thread = Thread(target=commit_concurrently)
    failing_thread.start()
    committed_thread.start()
    failing_thread.join(timeout=10)
    committed_thread.join(timeout=10)

    assert not failing_thread.is_alive()
    assert not committed_thread.is_alive()
    assert [record.operation for record in audit_log.records] == ["committed_operation"]


def test_routes_and_graph_expose_no_raw_context_authorized_write_seams() -> None:
    root = Path(__file__).resolve().parents[2]
    routes = (root / "src/devgraph/api/routes.py").read_text()
    enforcement = (root / "src/devgraph/auth/enforcement.py").read_text()

    assert "_authorized(" not in routes
    assert "def _create_work_object_authorized(" not in enforcement
    assert "AuthorityContext,\n        work_object" not in enforcement


def test_unregistered_write_session_cannot_mutate_even_with_private_constructor_token() -> None:
    from devgraph.auth import enforcement

    storage = MemoryGraphStorage()
    audit_log = AuditLog()
    verifier = SequencedVerifier(contexts=(AuthorityContext(envelope("registered")),))
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        repository=WorkObjectRepository(storage),
        initiative_observations=InitiativeObservationRepository(storage),
        monitor_storage=storage,
        audience=AUDIENCE,
        audit_log=audit_log,
    )
    forged = enforcement._GraphWriteSession(
        graph,
        enforcement._WRITE_SESSION_CONSTRUCTOR,
    )

    with pytest.raises(ForbiddenError, match="invalid write session"):
        forged.create_work_object(Issue(id="issue-forged-session", title="Must not persist"))

    assert storage.query() == []
    assert audit_log.records == []


def build_direct_graph() -> tuple[AuthorizedWorkGraph, MemoryGraphStorage, AuditLog]:
    storage = MemoryGraphStorage()
    audit_log = AuditLog()
    verifier = SequencedVerifier(contexts=(AuthorityContext(envelope("direct")),))
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        repository=WorkObjectRepository(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
    )
    return graph, storage, audit_log


def test_write_session_consumption_is_atomic_across_threads() -> None:
    graph, storage, audit_log = build_direct_graph()
    session = graph.authorize_write(CREDENTIAL)
    original_validate = graph._validate_write_session
    both_validated = Barrier(2)
    successes: list[str] = []
    failures: list[Exception] = []

    def synchronized_validation(candidate):
        context = original_validate(candidate)
        both_validated.wait(timeout=5)
        return context

    graph._validate_write_session = synchronized_validation  # type: ignore[method-assign]

    def consume(index: int) -> None:
        try:
            result = session.create_work_object(
                Issue(id=f"issue-session-race-{index}", title="Atomic consume")
            )
            successes.append(result.id)
        except Exception as exc:
            failures.append(exc)

    threads = [Thread(target=consume, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ForbiddenError)
    assert len(storage.query()) == 1
    assert len(audit_log.records) == 1


def test_verified_write_session_authority_is_immutable() -> None:
    graph, storage, audit_log = build_direct_graph()
    session = graph.authorize_write(CREDENTIAL)
    verified = session.authority_context

    with pytest.raises(AttributeError):
        session._authority_context = AuthorityContext(envelope("forged"))  # type: ignore[attr-defined]

    created = session.create_work_object(
        Issue(id="issue-immutable-authority", title="Immutable authority")
    )
    assert created.id == "issue-immutable-authority"
    assert audit_log.records[0].actor_id == verified.actor_id
    assert len(storage.query()) == 1


def test_async_audit_transactions_are_execution_context_isolated() -> None:
    audit_log = AuditLog()

    async def exercise() -> None:
        failed_recorded = asyncio.Event()
        committed = asyncio.Event()

        async def fail_after_peer_commit() -> None:
            try:
                with audit_log.transaction():
                    audit_log.record(
                        actor_id="actor-async-failed",
                        session_id="session-async-failed",
                        correlation_id="correlation-async-failed",
                        category="write",
                        operation="async_failed",
                    )
                    failed_recorded.set()
                    await asyncio.wait_for(committed.wait(), timeout=5)
                    raise RuntimeError("synthetic async rollback")
            except RuntimeError:
                pass

        async def commit_peer() -> None:
            await asyncio.wait_for(failed_recorded.wait(), timeout=5)
            with audit_log.transaction():
                audit_log.record(
                    actor_id="actor-async-committed",
                    session_id="session-async-committed",
                    correlation_id="correlation-async-committed",
                    category="write",
                    operation="async_committed",
                )
            committed.set()

        await asyncio.gather(fail_after_peer_commit(), commit_peer())

    asyncio.run(exercise())

    assert [record.operation for record in audit_log.records] == ["async_committed"]


def test_cancelled_audit_transaction_restores_execution_context() -> None:
    audit_log = AuditLog()

    async def exercise() -> None:
        try:
            with audit_log.transaction():
                audit_log.record(
                    actor_id="actor-cancelled",
                    session_id="session-cancelled",
                    correlation_id="correlation-cancelled",
                    category="write",
                    operation="cancelled_pending",
                )
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

        audit_log.record(
            actor_id="actor-recovered",
            session_id="session-recovered",
            correlation_id="correlation-recovered",
            category="write",
            operation="after_cancellation",
        )

    asyncio.run(exercise())

    assert [record.operation for record in audit_log.records] == ["after_cancellation"]


def test_executor_fixture_does_not_cast_away_session_protocol() -> None:
    source = (Path(__file__).with_name("test_idempotency.py")).read_text()
    assert "cast(" not in source
