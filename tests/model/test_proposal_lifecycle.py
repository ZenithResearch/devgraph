from __future__ import annotations

import pytest

from devgraph.model.base import WorkStatus
from devgraph.model.lifecycle import ProposalLifecycle, ProposalLifecycleError
from devgraph.model.work import Decision, Issue, Proposal
from devgraph.storage.base import EdgeRecord
from devgraph.storage.memory import MemoryGraphStorage


class FailingConversionDecisionStorage(MemoryGraphStorage):
    def create_edge(
        self,
        from_label: str,
        from_id: str,
        relationship: str,
        to_label: str,
        to_id: str,
        properties: dict[str, object] | None = None,
    ) -> EdgeRecord:
        if relationship == "CONVERSION_DECIDED_BY":
            raise RuntimeError("simulated conversion provenance write failure")
        return super().create_edge(
            from_label, from_id, relationship, to_label, to_id, properties
        )


def test_accepting_proposal_requires_decision_provenance():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Implement L3a")
    lifecycle.create_proposal(proposal)

    with pytest.raises(ProposalLifecycleError, match="Decision provenance is required"):
        lifecycle.accept_proposal(proposal.id, decision=None)

    assert lifecycle.get_proposal(proposal.id).status == WorkStatus.DRAFT


def test_accepted_proposal_records_decision_edge_and_converts_to_issue():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(
        id="proposal-1",
        title="Implement L3a",
        artifact_ids=("artifact-1",),
        external_link_ids=("link-1",),
    )
    decision = Decision(id="decision-1", title="Accept L3a")

    lifecycle.create_proposal(proposal)
    accepted = lifecycle.accept_proposal(proposal.id, decision=decision)
    converted = lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert accepted.status == WorkStatus.ACCEPTED
    assert accepted.artifact_ids == proposal.artifact_ids
    assert accepted.external_link_ids == proposal.external_link_ids
    assert isinstance(converted, Issue)
    assert converted.id == "issue-1"
    assert converted.title == proposal.title
    assert converted.artifact_ids == proposal.artifact_ids
    assert converted.external_link_ids == proposal.external_link_ids
    assert storage.get_node("Decision", decision.id) is not None
    assert storage.get_node("Issue", converted.id) is not None


def test_archived_proposal_cannot_convert_to_accepted_work_edge():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Archive me")

    lifecycle.create_proposal(proposal)
    archived = lifecycle.archive_proposal(proposal.id)

    assert archived.status == WorkStatus.ARCHIVED
    with pytest.raises(ProposalLifecycleError, match="Only accepted proposals can convert"):
        lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert storage.get_node("Issue", "issue-1") is None


def test_conversion_records_provenance_through_acceptance_decision_without_mutating_proposal():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(
        id="proposal-1",
        title="Promote me",
        description="Keep this history intact",
        artifact_ids=("artifact-1",),
        external_link_ids=("link-1",),
    )
    decision = Decision(id="decision-1", title="Approve promotion")

    lifecycle.create_proposal(proposal)
    lifecycle.accept_proposal(proposal.id, decision=decision)
    converted = lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    conversion_edges = [
        edge for edge in storage.list_edges("CONVERTED_TO") if edge.from_id == proposal.id
    ]
    decision_edges = [
        edge
        for edge in storage.list_edges("CONVERSION_DECIDED_BY")
        if edge.from_label == "Issue" and edge.from_id == converted.id
    ]

    assert len(conversion_edges) == 1
    assert conversion_edges[0].from_label == "Proposal"
    assert conversion_edges[0].from_id == proposal.id
    assert conversion_edges[0].to_label == "Issue"
    assert conversion_edges[0].to_id == converted.id
    assert conversion_edges[0].properties == {"decision_id": decision.id}
    assert len(decision_edges) == 1
    assert decision_edges[0].to_label == "Decision"
    assert decision_edges[0].to_id == decision.id
    assert decision_edges[0].properties == {"source_proposal_id": proposal.id}
    assert lifecycle.get_proposal(proposal.id).status == WorkStatus.ACCEPTED
    assert lifecycle.get_proposal(proposal.id).description == proposal.description


def test_conversion_inherits_proposal_priority():
    # Decision doc 0018 (operator-resolved 2026-07-06): conversion
    # preserves content, so a converted Issue carries the accepted
    # Proposal's priority instead of silently resetting to 0.
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Priority carries", priority=7)
    decision = Decision(id="decision-1", title="Approve")

    lifecycle.create_proposal(proposal)
    lifecycle.accept_proposal(proposal.id, decision=decision)
    issue = lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert issue.priority == 7
    stored = storage.get_node("Issue", "issue-1")
    assert stored is not None and stored.properties["priority"] == 7


class FailingAcceptanceDecisionStorage(MemoryGraphStorage):
    def create_edge(
        self,
        from_label: str,
        from_id: str,
        relationship: str,
        to_label: str,
        to_id: str,
        properties: dict[str, object] | None = None,
    ) -> EdgeRecord:
        if relationship == "ACCEPTED_BY_DECISION":
            raise RuntimeError("simulated acceptance provenance write failure")
        return super().create_edge(
            from_label, from_id, relationship, to_label, to_id, properties
        )


def test_acceptance_rolls_back_status_and_decision_when_provenance_edge_write_fails():
    storage = FailingAcceptanceDecisionStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Accept atomically")
    decision = Decision(id="decision-1", title="Approve acceptance")

    lifecycle.create_proposal(proposal)

    with pytest.raises(RuntimeError, match="simulated acceptance provenance write failure"):
        lifecycle.accept_proposal(proposal.id, decision=decision)

    # A crash between the status update and the Decision edge must not
    # strand an ACCEPTED proposal without provenance — conversion would
    # brick on it. All three writes roll back together.
    assert lifecycle.get_proposal(proposal.id).status == WorkStatus.DRAFT
    assert storage.get_node("Decision", "decision-1") is None
    assert storage.list_edges("ACCEPTED_BY_DECISION") == []


def test_conversion_rolls_back_issue_and_edges_when_provenance_edge_write_fails():
    storage = FailingConversionDecisionStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Promote atomically")
    decision = Decision(id="decision-1", title="Approve promotion")

    lifecycle.create_proposal(proposal)
    lifecycle.accept_proposal(proposal.id, decision=decision)

    with pytest.raises(RuntimeError, match="simulated conversion provenance write failure"):
        lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert storage.get_node("Issue", "issue-1") is None
    assert [
        edge
        for edge in storage.list_edges("CONVERTED_TO")
        if edge.from_id == proposal.id
    ] == []
    assert [
        edge
        for edge in storage.list_edges("CONVERSION_DECIDED_BY")
        if edge.from_id == "issue-1"
    ] == []


def test_reaccepting_proposal_fails_instead_of_adding_ambiguous_decision_edges():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="One decision only")
    first_decision = Decision(id="decision-1", title="First decision")
    second_decision = Decision(id="decision-2", title="Second decision")

    lifecycle.create_proposal(proposal)
    lifecycle.accept_proposal(proposal.id, decision=first_decision)

    with pytest.raises(
        ProposalLifecycleError,
        match="Accepted Proposal already has Decision provenance",
    ):
        lifecycle.accept_proposal(proposal.id, decision=second_decision)

    decision_edges = [
        edge for edge in storage.list_edges("ACCEPTED_BY_DECISION") if edge.from_id == proposal.id
    ]
    assert len(decision_edges) == 1
    assert decision_edges[0].to_id == first_decision.id


def test_conversion_fails_closed_when_existing_decision_provenance_is_ambiguous():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Ambiguous provenance")
    first_decision = Decision(id="decision-1", title="First decision")
    second_decision = Decision(id="decision-2", title="Second decision")

    lifecycle.create_proposal(proposal)
    lifecycle.accept_proposal(proposal.id, decision=first_decision)
    storage.create_node(
        second_decision.kind,
        second_decision.id,
        second_decision.to_node_properties(),
    )
    storage.create_edge(
        "Proposal",
        proposal.id,
        "ACCEPTED_BY_DECISION",
        "Decision",
        second_decision.id,
        {"required": True},
    )

    with pytest.raises(ProposalLifecycleError, match="ambiguous Decision provenance"):
        lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert storage.get_node("Issue", "issue-1") is None


def test_conversion_fails_closed_when_accepted_status_lacks_decision_provenance_edge():
    storage = MemoryGraphStorage()
    lifecycle = ProposalLifecycle(storage)
    proposal = Proposal(id="proposal-1", title="Unsafe accepted proposal")

    lifecycle.create_proposal(proposal)
    storage.update_node(
        "Proposal",
        proposal.id,
        proposal.with_status(WorkStatus.ACCEPTED).to_node_properties(),
    )

    with pytest.raises(ProposalLifecycleError, match="Accepted Proposal lacks Decision provenance"):
        lifecycle.convert_accepted_proposal_to_issue(proposal.id, issue_id="issue-1")

    assert storage.get_node("Issue", "issue-1") is None


def test_active_accepted_and_archived_fixture_builders_cover_lifecycle_examples():
    lifecycle = ProposalLifecycle(MemoryGraphStorage())

    active = lifecycle.example_active_proposal()
    accepted = lifecycle.example_accepted_by_decision_proposal()
    archived = lifecycle.example_archived_proposal()

    assert active.status == WorkStatus.DRAFT
    assert accepted.status == WorkStatus.ACCEPTED
    assert archived.status == WorkStatus.ARCHIVED
