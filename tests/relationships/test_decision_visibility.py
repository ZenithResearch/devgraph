from __future__ import annotations

from dataclasses import dataclass

import pytest

from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import (
    AcceptanceCriterion,
    Decision,
    Handoff,
    Initiative,
    Issue,
    Project,
    Proposal,
    Requirement,
    Task,
)
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

_FIXTURE_RELATIONSHIPS = {
    "HAS_CHILD",
    "BLOCKS",
    "HAS_REQUIREMENT",
    "HAS_ACCEPTANCE_CRITERION",
    "ACCEPTED_BY_DECISION",
    "CONVERTED_TO",
    "CONVERSION_DECIDED_BY",
    "HAS_HANDOFF",
    "HAS_READINESS_ASSESSMENT",
}


@dataclass(frozen=True)
class DecisionVisibilityFixture:
    storage: MemoryGraphStorage
    graph: RelationshipGraph
    initiative: Initiative
    project: Project
    issue: Issue
    task: Task
    blocked_task: Task
    proposal: Proposal
    requirement: Requirement
    acceptance_criterion: AcceptanceCriterion
    decision: Decision
    handoff: Handoff
    readiness_assessment_id: str


def build_decision_visibility_fixture() -> DecisionVisibilityFixture:
    """Concrete fixture graph from decision doc 0008, reusable by downstream tests.

    Initiative → Project → Issue → Task parentage, Task BLOCKS Task,
    Proposal → Requirement → AcceptanceCriterion, Proposal accepted and
    converted through a Decision (the converted Issue is the parentage Issue),
    Issue HAS_HANDOFF Handoff, and Issue HAS_READINESS_ASSESSMENT evidence.
    """
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)
    lifecycle = ProposalLifecycle(storage)

    proposal = Proposal(id="proposal-visibility", title="Make decision edges visible")
    requirement = Requirement(id="requirement-1", title="Decision edges must export")
    acceptance_criterion = AcceptanceCriterion(
        id="criterion-1", title="Decision edges appear in full edge listing"
    )
    decision = Decision(id="decision-1", title="Accept visibility proposal")

    lifecycle.create_proposal(proposal)
    graph.add_work_object(requirement)
    graph.add_work_object(acceptance_criterion)
    graph.add_requirement(proposal, requirement)
    graph.add_acceptance_criterion(requirement, acceptance_criterion)

    accepted_proposal = lifecycle.accept_proposal(proposal.id, decision=decision)
    issue = lifecycle.convert_accepted_proposal_to_issue(
        proposal.id, issue_id="issue-converted"
    )

    initiative = Initiative(id="initiative-1", title="Visibility initiative")
    project = Project(id="project-1", title="Visibility project")
    task = Task(id="task-1", title="Blocking task")
    blocked_task = Task(id="task-2", title="Blocked task")
    handoff = Handoff(id="handoff-1", title="Implementation handoff")
    for work_object in (initiative, project, task, blocked_task, handoff):
        graph.add_work_object(work_object)

    graph.add_parent(project, initiative)
    graph.add_parent(issue, project)
    graph.add_parent(task, issue)
    graph.add_parent(blocked_task, issue)
    graph.add_blocker(task, blocked_task)
    graph.add_handoff(issue, handoff)

    # Readiness evidence stays storage-level in v0: no ReadinessAssessment model
    # class exists here (Issue 2A owns that schema); the label is valid per
    # ontology/classes.md and the edge is evidence traversal only.
    readiness_assessment_id = "readiness-1"
    storage.create_node(
        "ReadinessAssessment",
        readiness_assessment_id,
        {"objective_clarity": 8, "verification_executability": 8},
    )
    storage.create_edge(
        issue.kind,
        issue.id,
        "HAS_READINESS_ASSESSMENT",
        "ReadinessAssessment",
        readiness_assessment_id,
    )

    return DecisionVisibilityFixture(
        storage=storage,
        graph=graph,
        initiative=initiative,
        project=project,
        issue=issue,
        task=task,
        blocked_task=blocked_task,
        proposal=accepted_proposal,
        requirement=requirement,
        acceptance_criterion=acceptance_criterion,
        decision=decision,
        handoff=handoff,
        readiness_assessment_id=readiness_assessment_id,
    )


def test_accepted_by_decision_edge_appears_in_full_edge_listing():
    fixture = build_decision_visibility_fixture()

    all_edges = fixture.storage.list_edges()
    acceptance_edges = [
        edge for edge in all_edges if edge.relationship == "ACCEPTED_BY_DECISION"
    ]

    assert len(acceptance_edges) == 1
    edge = acceptance_edges[0]
    assert edge.from_label == "Proposal"
    assert edge.from_id == fixture.proposal.id
    assert edge.to_label == "Decision"
    assert edge.to_id == fixture.decision.id


def test_converted_to_edge_is_visible_with_decision_id_property_intact():
    fixture = build_decision_visibility_fixture()

    conversion_edges = [
        edge
        for edge in fixture.storage.list_edges()
        if edge.relationship == "CONVERTED_TO"
    ]

    assert len(conversion_edges) == 1
    edge = conversion_edges[0]
    assert edge.from_label == "Proposal"
    assert edge.from_id == fixture.proposal.id
    assert edge.to_label == "Issue"
    assert edge.to_id == fixture.issue.id
    assert edge.properties == {"decision_id": fixture.decision.id}


def test_conversion_decided_by_edge_is_visible_with_source_proposal_id_intact():
    fixture = build_decision_visibility_fixture()

    provenance_edges = [
        edge
        for edge in fixture.storage.list_edges()
        if edge.relationship == "CONVERSION_DECIDED_BY"
    ]

    assert len(provenance_edges) == 1
    edge = provenance_edges[0]
    assert edge.from_label == "Issue"
    assert edge.from_id == fixture.issue.id
    assert edge.to_label == "Decision"
    assert edge.to_id == fixture.decision.id
    assert edge.properties == {"source_proposal_id": fixture.proposal.id}


def test_decision_node_is_retrievable_from_storage():
    fixture = build_decision_visibility_fixture()

    node = fixture.storage.get_node("Decision", fixture.decision.id)

    assert node is not None
    assert node.label == "Decision"
    assert node.properties["title"] == fixture.decision.title
    assert node.archived is False


def test_traversal_reaches_decisions_from_converted_issue_and_accepted_proposal():
    fixture = build_decision_visibility_fixture()

    assert fixture.graph.conversion_decision_for(fixture.issue) == fixture.decision
    assert fixture.graph.acceptance_decision_for(fixture.proposal) == fixture.decision


def test_decision_traversal_returns_none_when_no_decision_edges_exist():
    graph = RelationshipGraph(MemoryGraphStorage())
    proposal = Proposal(id="proposal-plain", title="No decision yet")
    issue = Issue(id="issue-plain", title="Issue without conversion provenance")
    graph.add_work_object(proposal)
    graph.add_work_object(issue)

    assert graph.acceptance_decision_for(proposal) is None
    assert graph.conversion_decision_for(issue) is None


def test_decision_traversal_rejects_invalid_source_types():
    fixture = build_decision_visibility_fixture()

    with pytest.raises(
        ValueError, match="ACCEPTED_BY_DECISION relationships must start from Proposal"
    ):
        fixture.graph.acceptance_decision_for(fixture.issue)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError, match="CONVERSION_DECIDED_BY relationships must start from Issue"
    ):
        fixture.graph.conversion_decision_for(fixture.proposal)  # type: ignore[arg-type]


def test_full_fixture_edge_listing_exposes_every_relationship_type():
    fixture = build_decision_visibility_fixture()

    listed_relationships = {
        edge.relationship for edge in fixture.storage.list_edges()
    }

    assert listed_relationships == _FIXTURE_RELATIONSHIPS
