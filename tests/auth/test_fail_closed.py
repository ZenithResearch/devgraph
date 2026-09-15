"""Fail-closed, production-like acceptance tests for Issue #7.

Proves the acceptance criteria against repo-local artifacts only:
unauthorized production-like mutation fails, devgraph does not mint
canonical identity, and local and remote access share the same scoped
authorization contract. Denial and allow smokes mirror the issue's
manual verification commands, in-process.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from devgraph.auth import (
    ALL_SCOPES,
    AuditLog,
    AuthorityContext,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    CredentialVerifier,
    LocalDevVerifier,
    UnauthenticatedError,
)
from devgraph.model.base import WorkStatus, utc_now
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import Decision, Issue, Proposal, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

FAKE_CREDENTIAL = "fake-credential-alpha"
AUDIENCE = "devgraph"

PRODUCTION_LIKE_MODES = pytest.mark.parametrize(
    "auth_mode", [None, "production"], ids=["unset", "production"]
)


def _envelope(scopes: frozenset[str] = frozenset(ALL_SCOPES)) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        scopes=scopes,
        expires_at=utc_now() + timedelta(hours=1),
        issuer="devgraph-test-issuer",
        audience=AUDIENCE,
    )


class _Fixture:
    """One storage + real collaborators behind an AuthorizedWorkGraph."""

    def __init__(self, verifier: CredentialVerifier) -> None:
        self.storage = MemoryGraphStorage()
        self.relationships = RelationshipGraph(self.storage)
        self.lifecycle = ProposalLifecycle(self.storage)
        self.audit_log = AuditLog()
        self.graph = AuthorizedWorkGraph(
            verifier,
            relationships=self.relationships,
            lifecycle=self.lifecycle,
            audience=AUDIENCE,
            audit_log=self.audit_log,
        )

    def seed_work_objects(self) -> tuple[Issue, Task]:
        issue = Issue(id="issue-1", title="Parent Issue")
        task = Task(id="task-1", title="Child Task")
        self.relationships.add_work_object(issue)
        self.relationships.add_work_object(task)
        self.relationships.add_parent(task, issue)
        self.lifecycle.create_proposal(Proposal(id="prop-1", title="A Proposal"))
        return issue, task

    def assert_proposal_untouched(self) -> None:
        node = self.storage.get_node("Proposal", "prop-1")
        assert node.properties["status"] == WorkStatus.DRAFT.value
        assert node.properties["version"] == 1
        assert self.storage.list_edges("ACCEPTED_BY_DECISION") == []
        assert self.storage.list_edges("CONVERTED_TO") == []
        assert self.storage.get_node("Issue", "issue-9") is None
        assert self.storage.get_node("Decision", "dec-1") is None


def _fixture(auth_mode: str | None) -> _Fixture:
    verifier = LocalDevVerifier(auth_mode=auth_mode)
    verifier.register(FAKE_CREDENTIAL, _envelope())
    return _Fixture(verifier)


def _all_operations(fixture: _Fixture, issue: Issue, task: Task, credential: str | None):
    decision = Decision(id="dec-1", title="Accept Proposal")
    return {
        "children_of": lambda: fixture.graph.children_of(credential, issue),
        "blockers_for": lambda: fixture.graph.blockers_for(credential, task),
        "accept_proposal": lambda: fixture.graph.accept_proposal(
            credential, "prop-1", decision
        ),
        "convert_accepted_proposal_to_issue": (
            lambda: fixture.graph.convert_accepted_proposal_to_issue(
                credential, "prop-1", "issue-9"
            )
        ),
    }


# --- production-like posture: everything fails closed ---------------------


@PRODUCTION_LIKE_MODES
def test_production_like_mode_denies_every_operation_even_with_full_scopes(auth_mode):
    fixture = _fixture(auth_mode)
    issue, task = fixture.seed_work_objects()

    for name, operation in _all_operations(fixture, issue, task, FAKE_CREDENTIAL).items():
        with pytest.raises(UnauthenticatedError) as exc_info:
            operation()
        assert exc_info.value.category == "unauthenticated", name

    fixture.assert_proposal_untouched()
    assert fixture.audit_log.records == []


@PRODUCTION_LIKE_MODES
def test_unauthorized_production_like_mutation_leaves_proposal_untouched(auth_mode):
    fixture = _fixture(auth_mode)
    fixture.seed_work_objects()
    decision = Decision(id="dec-1", title="Accept Proposal")

    with pytest.raises(UnauthenticatedError):
        fixture.graph.accept_proposal(FAKE_CREDENTIAL, "prop-1", decision)
    with pytest.raises(UnauthenticatedError):
        fixture.graph.convert_accepted_proposal_to_issue(
            FAKE_CREDENTIAL, "prop-1", "issue-9"
        )

    fixture.assert_proposal_untouched()
    assert fixture.audit_log.records == []


# --- issue verification smokes, in-process ---------------------------------


def test_denial_smoke_no_credential_yields_safe_unauthenticated_problem_detail():
    fixture = _fixture("local-dev")
    issue, _ = fixture.seed_work_objects()

    with pytest.raises(UnauthenticatedError) as exc_info:
        fixture.graph.children_of(None, issue)

    error = exc_info.value
    assert error.category == "unauthenticated"
    assert "required" in str(error)
    assert FAKE_CREDENTIAL not in str(error)
    assert fixture.audit_log.records == []


def test_allow_smoke_scoped_dev_credential_succeeds_and_records_audit_context():
    fixture = _fixture("local-dev")
    issue, _ = fixture.seed_work_objects()

    children = fixture.graph.children_of(FAKE_CREDENTIAL, issue)

    assert [child.id for child in children] == ["task-1"]
    assert len(fixture.audit_log.records) == 1
    record = fixture.audit_log.records[0]
    assert record.actor_id == "agent-frank"
    assert record.session_id == "session-0001"
    assert record.correlation_id == "corr-0001"


# --- devgraph does not mint canonical identity ------------------------------


def test_local_dev_verifier_offers_no_identity_minting_surface():
    public_methods = {
        name
        for name, _ in inspect.getmembers(LocalDevVerifier, predicate=callable)
        if not name.startswith("_")
    }
    assert public_methods == {"from_env", "register", "verify"}


def test_unregistered_credential_is_always_denied_never_minted():
    fixture = _fixture("local-dev")
    issue, _ = fixture.seed_work_objects()

    with pytest.raises(UnauthenticatedError) as exc_info:
        fixture.graph.children_of("fake-credential-unregistered", issue)

    assert "fake-credential-unregistered" not in str(exc_info.value)
    assert fixture.audit_log.records == []


# --- local and remote share the same authorization contract -----------------


class RecordingVerifier:
    """Stand-in for a future remote adapter (secS/Dregg/macaroon):
    same CredentialVerifier surface, records every verify call."""

    def __init__(self, envelope: CredentialEnvelope) -> None:
        self._envelope = envelope
        self.calls: list[tuple[str | None, str, datetime | None]] = []

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext:
        self.calls.append((credential, audience, now))
        return AuthorityContext(envelope=self._envelope)


def test_same_facade_call_path_works_identically_across_verifier_implementations():
    local_verifier = LocalDevVerifier(auth_mode="local-dev")
    local_verifier.register(FAKE_CREDENTIAL, _envelope())
    remote_style_verifier = RecordingVerifier(_envelope())
    assert isinstance(remote_style_verifier, CredentialVerifier)

    results = {}
    audit_records = {}
    for name, verifier in (
        ("local", local_verifier),
        ("remote-style", remote_style_verifier),
    ):
        fixture = _Fixture(verifier)
        issue, _ = fixture.seed_work_objects()
        results[name] = [child.id for child in fixture.graph.children_of(FAKE_CREDENTIAL, issue)]
        audit_records[name] = fixture.audit_log.records

    assert results["local"] == results["remote-style"] == ["task-1"]
    assert audit_records["local"] == audit_records["remote-style"]
    # verification happened per call, through the seam, with the facade audience
    assert remote_style_verifier.calls == [(FAKE_CREDENTIAL, AUDIENCE, None)]
