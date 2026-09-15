from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from devgraph.auth import (
    ALL_SCOPES,
    CATEGORY_READ,
    CATEGORY_WRITE,
    SCOPE_EXPORT_INTERNAL,
    SCOPE_READ,
    SCOPE_WRITE,
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    ForbiddenError,
    LocalDevVerifier,
    UnauthenticatedError,
)
from devgraph.auth.context import AuthorityContext
from devgraph.events.outbox import EventOutbox
from devgraph.model.base import WorkStatus, utc_now
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import (
    InvalidStatusTransitionError,
    WorkObjectRepository,
    WorkObjectVersionConflictError,
)
from devgraph.model.validation import SIGNED_64_MAX, NumericBoundError
from devgraph.model.work import Proposal, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

FAKE_CREDENTIAL = "fake-credential-alpha"
AUDIENCE = "devgraph"


class SpyRepository(WorkObjectRepository):
    """Counts delegate calls to prove denied operations never reach it."""

    def __init__(self, storage: MemoryGraphStorage) -> None:
        super().__init__(storage)
        self.calls: list[str] = []

    def get_by_id(self, kind, work_object_id):
        self.calls.append("get_by_id")
        return super().get_by_id(kind, work_object_id)

    def query(
        self,
        kind,
        *,
        include_archived=False,
        descending=False,
        after_id=None,
        limit=50,
    ):
        self.calls.append("query")
        return super().query(
            kind,
            include_archived=include_archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )

    def create(self, work_object):
        self.calls.append("create")
        return super().create(work_object)

    def update(self, work_object, *, expected_version):
        self.calls.append("update")
        return super().update(work_object, expected_version=expected_version)

    def update_content(self, kind, work_object_id, *, expected_version, changes):
        self.calls.append("update_content")
        return super().update_content(
            kind, work_object_id, expected_version=expected_version, changes=changes
        )

    def archive(self, kind, work_object_id):
        self.calls.append("archive")
        return super().archive(kind, work_object_id)

    def transition_status(self, kind, work_object_id, new_status):
        self.calls.append("transition_status")
        return super().transition_status(kind, work_object_id, new_status)


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
    task = Task(id=task_id, title="Seeded task")
    storage.create_node(task.kind, task.id, task.to_node_properties())
    return task


# --- authorized reads succeed with exactly the read scope ---------------


def test_get_work_object_succeeds_with_read_scope_and_audits():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_READ}))
    task = _seed_task(storage)

    loaded = graph.get_work_object(FAKE_CREDENTIAL, "Task", task.id)

    assert loaded == task
    assert repository.calls == ["get_by_id"]
    assert [record.operation for record in audit_log.records] == ["get_work_object"]
    assert audit_log.records[0].category == CATEGORY_READ


def test_query_work_objects_succeeds_with_read_scope_and_audits():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_READ}))
    task = _seed_task(storage)
    archived = _seed_task(storage, "task-2")
    repository.archive("Task", archived.id)
    repository.calls.clear()

    active = graph.query_work_objects(FAKE_CREDENTIAL, "Task")
    everything = graph.query_work_objects(FAKE_CREDENTIAL, "Task", include_archived=True)

    assert [item.id for item in active] == [task.id]
    assert {item.id for item in everything} == {task.id, archived.id}
    assert repository.calls == ["query", "query"]
    assert [record.operation for record in audit_log.records] == [
        "query_work_objects",
        "query_work_objects",
    ]


# --- scope matrix: every non-read scope fails closed --------------------


@pytest.mark.parametrize("scope", sorted(ALL_SCOPES - {SCOPE_READ}))
def test_read_operations_deny_every_other_scope_with_zero_side_effects(scope):
    graph, storage, repository, audit_log = _build_graph(frozenset({scope}))
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.get_work_object(FAKE_CREDENTIAL, "Task", "task-1")
    with pytest.raises(ForbiddenError):
        graph.query_work_objects(FAKE_CREDENTIAL, "Task")

    assert repository.calls == []
    assert audit_log.records == []


def test_query_rejects_invalid_keyset_before_delegate_or_audit() -> None:
    from devgraph.model.validation import InvalidWorkObjectId, NumericBoundError

    graph, _storage, repository, audit_log = _build_graph(frozenset({SCOPE_READ}))
    with pytest.raises(InvalidWorkObjectId):
        graph.query_work_objects(FAKE_CREDENTIAL, "Task", after_id="BAD")
    with pytest.raises(NumericBoundError):
        graph.query_work_objects(FAKE_CREDENTIAL, "Task", limit=True)
    assert repository.calls == []
    assert audit_log.records == []


def test_read_operations_deny_every_other_scope_even_all_combined():
    graph, storage, repository, audit_log = _build_graph(ALL_SCOPES - {SCOPE_READ})
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.get_work_object(FAKE_CREDENTIAL, "Task", "task-1")

    assert repository.calls == []
    assert audit_log.records == []


# --- per-call credential verification ------------------------------------


def test_unknown_credential_is_denied_before_the_delegate():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_READ}))
    _seed_task(storage)

    with pytest.raises(UnauthenticatedError):
        graph.get_work_object("unregistered-credential", "Task", "task-1")

    assert repository.calls == []
    assert audit_log.records == []


# --- authorized writes succeed with exactly the write scope -------------


def test_create_work_object_succeeds_with_write_scope_and_audits():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    task = Task(id="task-1", title="Created through facade")

    created = graph.create_work_object(FAKE_CREDENTIAL, task)

    assert created == task
    node = storage.get_node("Task", "task-1")
    assert node is not None
    assert repository.calls == ["create"]
    assert [record.operation for record in audit_log.records] == ["create_work_object"]
    assert audit_log.records[0].category == CATEGORY_WRITE


def test_create_work_object_cannot_create_accepted_proposal_without_provenance():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    accepted = Proposal(
        id="proposal-1",
        title="Accepted without Decision provenance",
        status=WorkStatus.ACCEPTED,
    )

    with pytest.raises(InvalidStatusTransitionError):
        graph.create_work_object(FAKE_CREDENTIAL, accepted)

    assert storage.get_node("Proposal", "proposal-1") is None
    assert repository.calls == ["create"]
    assert audit_log.records == []


def test_update_work_object_propagates_version_precondition_and_safe_conflict():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    task = graph.create_work_object(FAKE_CREDENTIAL, Task(id="task-1", title="v1"))

    updated = graph.update_work_object(
        FAKE_CREDENTIAL, replace(task, title="v2"), expected_version=1
    )
    assert updated.version == 2

    with pytest.raises(WorkObjectVersionConflictError) as excinfo:
        graph.update_work_object(FAKE_CREDENTIAL, replace(task, title="stale"), expected_version=1)

    assert excinfo.value.actual_version == 2
    assert "stale" not in str(excinfo.value)
    node = storage.get_node("Task", "task-1")
    assert node is not None and node.properties["title"] == "v2"
    # conflict happened after authorization: the delegate was reached but
    # only the successful writes were audited
    assert [record.operation for record in audit_log.records] == [
        "create_work_object",
        "update_work_object",
    ]


def test_max_version_archive_fails_without_success_audit_or_mutation() -> None:
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    task = Task(id="task-1", title="At max", version=SIGNED_64_MAX)
    storage.create_node(task.kind, task.id, task.to_node_properties())
    before = storage.get_node(task.kind, task.id)

    with pytest.raises(NumericBoundError):
        graph.archive_work_object(FAKE_CREDENTIAL, task.kind, task.id)

    assert storage.get_node(task.kind, task.id) == before
    assert repository.calls == ["archive", "get_by_id"]
    assert audit_log.records == []


def test_archive_work_object_succeeds_with_write_scope():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    graph.create_work_object(FAKE_CREDENTIAL, Task(id="task-1", title="Archive me"))

    archived = graph.archive_work_object(FAKE_CREDENTIAL, "Task", "task-1")

    assert archived.status == WorkStatus.ARCHIVED
    node = storage.get_node("Task", "task-1")
    assert node is not None and node.archived is True
    assert audit_log.records[-1].operation == "archive_work_object"


def test_transition_work_object_status_follows_table_through_facade():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    graph.create_work_object(FAKE_CREDENTIAL, Task(id="task-1", title="Move me"))

    in_review = graph.transition_work_object_status(
        FAKE_CREDENTIAL, "Task", "task-1", WorkStatus.REVIEW
    )

    assert in_review.status == WorkStatus.REVIEW
    assert audit_log.records[-1].operation == "transition_work_object_status"


def test_transition_cannot_reach_accepted_for_proposals_through_facade():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    graph.create_work_object(FAKE_CREDENTIAL, Proposal(id="proposal-1", title="Needs provenance"))

    with pytest.raises(InvalidStatusTransitionError):
        graph.transition_work_object_status(
            FAKE_CREDENTIAL, "Proposal", "proposal-1", WorkStatus.ACCEPTED
        )

    node = storage.get_node("Proposal", "proposal-1")
    assert node is not None
    assert node.properties["status"] == WorkStatus.DRAFT.value


# --- scope matrix: every non-write scope fails closed --------------------


@pytest.mark.parametrize("scope", sorted(ALL_SCOPES - {SCOPE_WRITE}))
def test_write_operations_deny_every_other_scope_with_zero_side_effects(scope):
    graph, storage, repository, audit_log = _build_graph(frozenset({scope}))
    _seed_task(storage)

    with pytest.raises(ForbiddenError):
        graph.create_work_object(FAKE_CREDENTIAL, Task(id="task-new", title="Denied"))
    with pytest.raises(ForbiddenError):
        graph.update_work_object(
            FAKE_CREDENTIAL, Task(id="task-1", title="Denied"), expected_version=1
        )
    with pytest.raises(ForbiddenError):
        graph.archive_work_object(FAKE_CREDENTIAL, "Task", "task-1")
    with pytest.raises(ForbiddenError):
        graph.transition_work_object_status(FAKE_CREDENTIAL, "Task", "task-1", WorkStatus.REVIEW)

    assert repository.calls == []
    assert audit_log.records == []
    assert storage.get_node("Task", "task-new") is None
    node = storage.get_node("Task", "task-1")
    assert node is not None and node.properties["status"] == WorkStatus.DRAFT.value


# --- outbox compatibility -------------------------------------------------


def test_facade_write_is_callable_inside_record_mutation_with_receipt():
    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    outbox = EventOutbox(storage, receipt_id_factory=lambda: "receipt-1")
    authority = AuthorityContext(envelope=_envelope(frozenset({SCOPE_WRITE})))

    result, receipt = outbox.record_mutation_with_receipt(
        authority=authority,
        operation="create_work_object",
        subject_label="Task",
        subject_id="task-1",
        idempotency_key="synthetic-idempotency-key",
        summary={"message": "facade create inside outbox"},
        mutation=lambda _storage: graph.create_work_object(
            FAKE_CREDENTIAL, Task(id="task-1", title="Outbox wrapped")
        ),
    )

    assert result == Task(
        id="task-1",
        title="Outbox wrapped",
        created_at=result.created_at,
        updated_at=result.updated_at,
    )
    assert storage.get_node("Task", "task-1") is not None
    assert storage.get_node("EventReceipt", receipt.id) is not None
    assert repository.calls == ["create"]
    assert [record.operation for record in audit_log.records] == ["create_work_object"]


def test_facade_rejects_invalid_ids_before_repository_delegate_or_audit() -> None:
    from devgraph.model.validation import InvalidWorkObjectId

    graph, _storage, repository, audit_log = _build_graph(
        frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_EXPORT_INTERNAL})
    )
    for operation in (
        lambda: graph.get_work_object(FAKE_CREDENTIAL, "Task", "BAD"),
        lambda: graph.archive_work_object(FAKE_CREDENTIAL, "Task", "bad_underscore"),
        lambda: graph.transition_work_object_status(
            FAKE_CREDENTIAL, "Task", "bad space", WorkStatus.REVIEW
        ),
        lambda: graph.export_internal_by_ids(FAKE_CREDENTIAL, "Task", ["ok", "BAD"]),
    ):
        with pytest.raises(InvalidWorkObjectId):
            operation()
    assert repository.calls == []
    assert audit_log.records == []


def test_facade_rejects_invalid_expected_version_before_delegate_or_audit() -> None:
    from devgraph.model.repository import ContentChanges
    from devgraph.model.validation import NumericBoundError

    graph, _storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    valid = Task(id="task-1", title="valid")
    for operation in (
        lambda: graph.update_work_object(FAKE_CREDENTIAL, valid, expected_version=True),
        lambda: graph.update_work_object_content(
            FAKE_CREDENTIAL,
            "Task",
            "task-1",
            expected_version=0,
            changes=ContentChanges(title="x"),
        ),
    ):
        with pytest.raises(NumericBoundError):
            operation()
    assert repository.calls == []
    assert audit_log.records == []


def test_missing_repository_is_a_wiring_error_not_a_silent_pass():
    storage = MemoryGraphStorage()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(FAKE_CREDENTIAL, _envelope(frozenset({SCOPE_READ})))
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=AuditLog(),
    )

    with pytest.raises(ValueError, match="repository is not configured"):
        graph.get_work_object(FAKE_CREDENTIAL, "Task", "task-1")


def test_update_content_write_only_delegates_once_and_audits_once() -> None:
    from devgraph.model.repository import ContentChanges

    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    _seed_task(storage)
    updated = graph.update_work_object_content(
        FAKE_CREDENTIAL,
        "Task",
        "task-1",
        expected_version=1,
        changes=ContentChanges(title="Updated"),
    )
    assert updated.title == "Updated"
    assert repository.calls == ["update_content"]
    assert [(r.category, r.operation) for r in audit_log.records] == [
        (CATEGORY_WRITE, "update_work_object_content")
    ]


@pytest.mark.parametrize("scope", sorted(ALL_SCOPES - {SCOPE_WRITE}))
def test_update_content_denies_every_non_write_scope_without_delegate_or_audit(scope) -> None:
    from devgraph.model.repository import ContentChanges

    graph, storage, repository, audit_log = _build_graph(frozenset({scope}))
    _seed_task(storage)
    with pytest.raises(ForbiddenError):
        graph.update_work_object_content(
            FAKE_CREDENTIAL,
            "Task",
            "task-1",
            expected_version=1,
            changes=ContentChanges(title="Denied"),
        )
    assert repository.calls == [] and audit_log.records == []


def test_update_content_conflict_has_no_success_audit_or_read_delegate() -> None:
    from devgraph.model.repository import ContentChanges

    graph, storage, repository, audit_log = _build_graph(frozenset({SCOPE_WRITE}))
    _seed_task(storage)
    with pytest.raises(WorkObjectVersionConflictError):
        graph.update_work_object_content(
            FAKE_CREDENTIAL,
            "Task",
            "task-1",
            expected_version=2,
            changes=ContentChanges(title="Stale"),
        )
    assert repository.calls == ["update_content"]
    assert audit_log.records == []
