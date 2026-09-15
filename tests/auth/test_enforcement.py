from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from devgraph.auth import (
    ALL_SCOPES,
    CATEGORY_READ,
    CATEGORY_WRITE,
    REQUIRED_SCOPE_BY_CATEGORY,
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    AuditLog,
    AuditRecord,
    AuthError,
    AuthorityContext,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    ForbiddenError,
    LocalDevVerifier,
    require_scope,
)
from devgraph.model.base import WorkStatus, utc_now
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import Decision, Issue, Proposal, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

FAKE_CREDENTIAL = "fake-credential-alpha"
AUDIENCE = "devgraph"


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


def _context(scopes: frozenset[str]) -> AuthorityContext:
    return AuthorityContext(envelope=_envelope(scopes))


def _build_authorized_graph(scopes: frozenset[str]):
    storage = MemoryGraphStorage()
    relationships = RelationshipGraph(storage)
    lifecycle = ProposalLifecycle(storage)
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(FAKE_CREDENTIAL, _envelope(scopes))
    audit_log = AuditLog()
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=relationships,
        lifecycle=lifecycle,
        audience=AUDIENCE,
        audit_log=audit_log,
    )
    return graph, storage, relationships, lifecycle, audit_log


# --- require_scope: deny-by-default policy matrix -----------------------


@pytest.mark.parametrize("category", sorted(REQUIRED_SCOPE_BY_CATEGORY))
def test_require_scope_grants_when_exact_required_scope_present(category):
    required = REQUIRED_SCOPE_BY_CATEGORY[category]

    require_scope(_context(frozenset({required})), category)


@pytest.mark.parametrize("category", sorted(REQUIRED_SCOPE_BY_CATEGORY))
def test_require_scope_denies_without_exact_scope_even_with_every_other_scope(category):
    required = REQUIRED_SCOPE_BY_CATEGORY[category]
    context = _context(frozenset(ALL_SCOPES - {required}))

    with pytest.raises(ForbiddenError):
        require_scope(context, category)


@pytest.mark.parametrize("category", [CATEGORY_READ, CATEGORY_WRITE])
def test_admin_scope_does_not_satisfy_read_or_write(category):
    context = _context(frozenset({SCOPE_ADMIN}))

    with pytest.raises(ForbiddenError):
        require_scope(context, category)


def test_require_scope_unknown_category_raises_value_error():
    with pytest.raises(ValueError, match="unknown operation category"):
        require_scope(_context(frozenset(ALL_SCOPES)), "delete")


def test_forbidden_error_is_safe_and_names_only_the_denied_category():
    context = _context(frozenset({SCOPE_READ}))

    with pytest.raises(ForbiddenError) as exc_info:
        require_scope(context, CATEGORY_WRITE)

    error = exc_info.value
    assert isinstance(error, AuthError)
    assert error.category == "forbidden"
    assert "write" in str(error)
    for rendered in (str(error), repr(error)):
        assert FAKE_CREDENTIAL not in rendered
        for scope in ALL_SCOPES:
            assert scope not in rendered


# --- audit records: safe fields only ------------------------------------


def test_audit_record_is_frozen_and_carries_only_safe_audit_fields():
    record = AuditRecord(
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        category=CATEGORY_READ,
        operation="children_of",
    )

    field_names = {field.name for field in dataclasses.fields(record)}
    assert field_names == {
        "actor_id",
        "session_id",
            "correlation_id",
            "category",
            "operation",
            "safe_summary",
        }
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.operation = "other"  # type: ignore[misc]


# --- AuthorizedWorkGraph: guarded read operations ------------------------


def test_children_of_with_read_scope_delegates_and_audits():
    graph, _, relationships, _, audit_log = _build_authorized_graph(
        frozenset({SCOPE_READ})
    )
    issue = Issue(id="issue-1", title="Parent Issue")
    task = Task(id="task-1", title="Child Task")
    relationships.add_work_object(issue)
    relationships.add_work_object(task)
    relationships.add_parent(task, issue)

    children = graph.children_of(FAKE_CREDENTIAL, issue)

    assert [child.id for child in children] == ["task-1"]
    assert audit_log.records == [
        AuditRecord(
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
            category=CATEGORY_READ,
            operation="children_of",
        )
    ]


def test_blockers_for_with_read_scope_delegates_and_audits():
    graph, _, relationships, _, audit_log = _build_authorized_graph(
        frozenset({SCOPE_READ})
    )
    blocking = Task(id="task-blocking", title="Blocking Task")
    blocked = Task(id="task-blocked", title="Blocked Task")
    relationships.add_work_object(blocking)
    relationships.add_work_object(blocked)
    relationships.add_blocker(blocking, blocked)

    blockers = graph.blockers_for(FAKE_CREDENTIAL, blocked)

    assert [blocker.id for blocker in blockers] == ["task-blocking"]
    record = audit_log.records[-1]
    assert record.category == CATEGORY_READ
    assert record.operation == "blockers_for"
    assert record.actor_id == "agent-frank"


# --- AuthorizedWorkGraph: guarded write operations ------------------------


def test_accept_proposal_with_write_scope_delegates_and_audits():
    graph, storage, _, lifecycle, audit_log = _build_authorized_graph(
        frozenset({SCOPE_WRITE})
    )
    lifecycle.create_proposal(Proposal(id="prop-1", title="A Proposal"))
    decision = Decision(id="dec-1", title="Accept Proposal")

    accepted = graph.accept_proposal(FAKE_CREDENTIAL, "prop-1", decision)

    assert accepted.status == WorkStatus.ACCEPTED
    node = storage.get_node("Proposal", "prop-1")
    assert node.properties["status"] == WorkStatus.ACCEPTED.value
    assert len(storage.list_edges("ACCEPTED_BY_DECISION")) == 1
    record = audit_log.records[-1]
    assert record.category == CATEGORY_WRITE
    assert record.operation == "accept_proposal"
    assert record.actor_id == "agent-frank"
    assert record.session_id == "session-0001"
    assert record.correlation_id == "corr-0001"


def test_convert_accepted_proposal_with_write_scope_delegates_and_audits():
    graph, storage, _, lifecycle, audit_log = _build_authorized_graph(
        frozenset({SCOPE_WRITE})
    )
    lifecycle.create_proposal(Proposal(id="prop-1", title="A Proposal"))
    lifecycle.accept_proposal("prop-1", Decision(id="dec-1", title="Accept Proposal"))

    issue = graph.convert_accepted_proposal_to_issue(FAKE_CREDENTIAL, "prop-1", "issue-9")

    assert issue.id == "issue-9"
    assert storage.get_node("Issue", "issue-9") is not None
    record = audit_log.records[-1]
    assert record.category == CATEGORY_WRITE
    assert record.operation == "convert_accepted_proposal_to_issue"


# --- denial leaves state untouched and unaudited --------------------------


def test_read_only_credential_denied_on_write_op_without_side_effects():
    graph, storage, _, lifecycle, audit_log = _build_authorized_graph(
        frozenset({SCOPE_READ})
    )
    lifecycle.create_proposal(Proposal(id="prop-1", title="A Proposal"))
    decision = Decision(id="dec-1", title="Accept Proposal")

    with pytest.raises(ForbiddenError) as exc_info:
        graph.accept_proposal(FAKE_CREDENTIAL, "prop-1", decision)

    assert exc_info.value.category == "forbidden"
    node = storage.get_node("Proposal", "prop-1")
    assert node.properties["status"] == WorkStatus.DRAFT.value
    assert node.properties["version"] == 1
    assert storage.list_edges("ACCEPTED_BY_DECISION") == []
    assert storage.get_node("Decision", "dec-1") is None
    assert audit_log.records == []


def test_write_only_credential_denied_on_read_op_without_audit_record():
    graph, _, relationships, _, audit_log = _build_authorized_graph(
        frozenset({SCOPE_WRITE})
    )
    issue = Issue(id="issue-1", title="Parent Issue")
    relationships.add_work_object(issue)

    with pytest.raises(ForbiddenError):
        graph.children_of(FAKE_CREDENTIAL, issue)

    assert audit_log.records == []
