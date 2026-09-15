from __future__ import annotations

from devgraph.model.work import AcceptanceCriterion, Issue, Requirement, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage


def test_work_object_requirement_edges_are_traversable_in_both_directions():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    issue = Issue(id="issue-req", title="Relationship traversal")
    requirement = Requirement(id="req-1", title="Requirement edges exist")

    graph.add_work_object(issue)
    graph.add_work_object(requirement)

    edge = graph.add_requirement(issue, requirement)

    assert edge.relationship == "HAS_REQUIREMENT"
    assert edge.from_label == "Issue"
    assert edge.from_id == issue.id
    assert edge.to_label == "Requirement"
    assert edge.to_id == requirement.id
    assert graph.requirements_for(issue) == [requirement]
    assert graph.work_objects_for_requirement(requirement) == [issue]


def test_acceptance_criterion_edges_are_traversable_from_requirement_and_work_object():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    issue = Issue(id="issue-ac", title="Acceptance traversal")
    task = Task(id="task-ac", title="Implement criterion helpers")
    requirement = Requirement(id="req-ac", title="Requirement has criteria")
    issue_criterion = AcceptanceCriterion(
        id="ac-issue", title="Issue exposes acceptance criterion"
    )
    requirement_criterion = AcceptanceCriterion(
        id="ac-req", title="Requirement exposes acceptance criterion"
    )

    for work_object in (
        issue,
        task,
        requirement,
        issue_criterion,
        requirement_criterion,
    ):
        graph.add_work_object(work_object)

    issue_edge = graph.add_acceptance_criterion(issue, issue_criterion)
    requirement_edge = graph.add_acceptance_criterion(
        requirement, requirement_criterion
    )

    assert issue_edge.relationship == "HAS_ACCEPTANCE_CRITERION"
    assert issue_edge.from_label == "Issue"
    assert issue_edge.to_label == "AcceptanceCriterion"
    assert requirement_edge.from_label == "Requirement"
    assert graph.acceptance_criteria_for(issue) == [issue_criterion]
    assert graph.acceptance_criteria_for(requirement) == [requirement_criterion]
    assert graph.objects_for_acceptance_criterion(issue_criterion) == [issue]
    assert graph.objects_for_acceptance_criterion(requirement_criterion) == [requirement]


def test_requirements_and_acceptance_criteria_reject_invalid_target_types():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    issue = Issue(id="issue-1", title="Invalid target checks")
    task = Task(id="task-1", title="Not a requirement or criterion")
    requirement = Requirement(id="req-1", title="Valid requirement")

    for work_object in (issue, task, requirement):
        graph.add_work_object(work_object)

    try:
        graph.add_requirement(issue, task)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_REQUIREMENT relationships must target Requirement" in str(exc)
    else:
        raise AssertionError("HAS_REQUIREMENT should reject non-Requirement targets")

    try:
        graph.add_acceptance_criterion(issue, requirement)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_ACCEPTANCE_CRITERION relationships must target AcceptanceCriterion" in str(exc)
    else:
        raise AssertionError(
            "HAS_ACCEPTANCE_CRITERION should reject non-AcceptanceCriterion targets"
        )


def test_requirements_and_acceptance_criteria_reject_invalid_source_types():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    requirement = Requirement(id="req-source", title="Not a work-object source")
    criterion = AcceptanceCriterion(id="ac-source", title="Not a source")

    graph.add_work_object(requirement)
    graph.add_work_object(criterion)

    try:
        graph.add_requirement(requirement, requirement)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_REQUIREMENT relationships must start from Todo-derived work" in str(
            exc
        )
    else:
        raise AssertionError("HAS_REQUIREMENT should reject non-Todo source nodes")

    try:
        graph.add_acceptance_criterion(criterion, criterion)  # type: ignore[arg-type]
    except ValueError as exc:
        assert (
            "HAS_ACCEPTANCE_CRITERION relationships must start from "
            "Todo-derived work or Requirement" in str(exc)
        )
    else:
        raise AssertionError(
            "HAS_ACCEPTANCE_CRITERION should reject AcceptanceCriterion source nodes"
        )
