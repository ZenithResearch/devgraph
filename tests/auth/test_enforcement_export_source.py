from __future__ import annotations

from datetime import timedelta

import pytest

from devgraph.auth import (
    ALL_SCOPES,
    SCOPE_EXPORT_INTERNAL,
    SCOPE_EXPORT_REDACTED,
    SCOPE_READ,
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    ForbiddenError,
    LocalDevVerifier,
)
from devgraph.model.base import utc_now
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import MissingWorkObjectError, WorkObjectRepository
from devgraph.model.work import Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

FAKE_CREDENTIAL = "fake-credential-alpha"
AUDIENCE = "devgraph"


class SpyRepository(WorkObjectRepository):
    def __init__(self, storage: MemoryGraphStorage) -> None:
        super().__init__(storage)
        self.calls: list[str] = []

    def get_by_id(self, kind, work_object_id):
        self.calls.append("get_by_id")
        return super().get_by_id(kind, work_object_id)


def _envelope(scopes: frozenset[str]) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        scopes=scopes,
        expires_at=utc_now() + timedelta(hours=1),
        issuer="devgraph-test-issuer",
        audience=AUDIENCE,
    )


def _build_graph(scopes: frozenset[str]):
    storage = MemoryGraphStorage()
    repository = SpyRepository(storage)
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(FAKE_CREDENTIAL, _envelope(scopes))
    audit_log = AuditLog()
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
        repository=repository,
    )
    return graph, storage, repository, audit_log


def _seed_task(storage: MemoryGraphStorage, task_id: str = "task-1") -> Task:
    task = Task(id=task_id, title="Export source task")
    storage.create_node(task.kind, task.id, task.to_node_properties())
    return task


# --- export-scoped credential needs no read scope ------------------------


def test_internal_export_by_ids_needs_only_the_internal_export_scope():
    graph, storage, repository, audit_log = _build_graph(
        frozenset({SCOPE_EXPORT_INTERNAL})
    )
    task = _seed_task(storage)

    result = graph.export_internal_by_ids(FAKE_CREDENTIAL, "Task", [task.id])

    assert result["mode"] == "internal"
    assert repository.calls == ["get_by_id"]
    assert [record.operation for record in audit_log.records] == [
        "export_internal_by_ids"
    ]


def test_redacted_and_public_summary_by_ids_need_only_the_redacted_scope():
    graph, storage, repository, audit_log = _build_graph(
        frozenset({SCOPE_EXPORT_REDACTED})
    )
    task = _seed_task(storage)

    redacted = graph.export_redacted_by_ids(FAKE_CREDENTIAL, "Task", [task.id])
    summary = graph.export_public_safe_summary_by_ids(
        FAKE_CREDENTIAL, "Task", [task.id]
    )

    assert redacted["mode"] == "redacted"
    assert summary["mode"] == "public_safe_summary"
    assert [record.operation for record in audit_log.records] == [
        "export_redacted_by_ids",
        "export_public_safe_summary_by_ids",
    ]


# --- read scope cannot export; export scopes stay mode-exact -------------


def test_read_scoped_credential_cannot_export_by_ids():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_READ}))
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.export_internal_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])
    with pytest.raises(ForbiddenError):
        graph.export_redacted_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])
    with pytest.raises(ForbiddenError):
        graph.export_public_safe_summary_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])

    assert repository.calls == []
    assert audit_log.records == []


def test_redacted_scope_cannot_internal_export_and_vice_versa():
    graph, storage, repository, audit_log = _build_graph(
        frozenset({SCOPE_EXPORT_REDACTED})
    )
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.export_internal_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])

    internal_only, storage2, repository2, audit_log2 = _build_graph(
        frozenset({SCOPE_EXPORT_INTERNAL})
    )
    _seed_task(storage2)

    with pytest.raises(ForbiddenError):
        internal_only.export_redacted_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])

    assert repository.calls == [] and repository2.calls == []
    assert audit_log.records == [] and audit_log2.records == []


@pytest.mark.parametrize(
    "scope", sorted(ALL_SCOPES - {SCOPE_EXPORT_INTERNAL, SCOPE_EXPORT_REDACTED})
)
def test_export_by_ids_denies_every_non_export_scope(scope):
    graph, storage, repository, audit_log = _build_graph(frozenset({scope}))
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.export_internal_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])
    with pytest.raises(ForbiddenError):
        graph.export_redacted_by_ids(FAKE_CREDENTIAL, "Task", ["task-1"])

    assert repository.calls == []
    assert audit_log.records == []


# --- fail closed on missing records: never a partial export --------------


def test_missing_id_fails_closed_with_no_export_output_and_no_audit():
    graph, storage, repository, audit_log = _build_graph(
        frozenset({SCOPE_EXPORT_INTERNAL})
    )
    _seed_task(storage)

    with pytest.raises(MissingWorkObjectError):
        graph.export_internal_by_ids(
            FAKE_CREDENTIAL, "Task", ["task-1", "task-missing"]
        )

    assert audit_log.records == []
