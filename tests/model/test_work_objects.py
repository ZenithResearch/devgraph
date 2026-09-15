from __future__ import annotations

from datetime import datetime

import pytest

import devgraph.model as model
from devgraph.model.base import WorkStatus
from devgraph.model.validation import SIGNED_64_MAX, InvalidWorkObjectId, NumericBoundError
from devgraph.model.work import (
    AcceptanceCriterion,
    Blocker,
    Decision,
    Handoff,
    Initiative,
    Issue,
    Milestone,
    Project,
    Proposal,
    Requirement,
    ReviewPacket,
    Task,
    Todo,
)


def test_todo_base_and_derived_work_objects_have_required_fields():
    proposal = Proposal(id="proposal-1", title="Adopt lifecycle rules")
    assert isinstance(proposal, Todo)
    assert proposal.id == "proposal-1"
    assert proposal.title == "Adopt lifecycle rules"
    assert proposal.description == ""
    assert proposal.status == WorkStatus.DRAFT
    assert proposal.version == 1
    assert proposal.created_at <= proposal.updated_at

    for cls in [Initiative, Project, Issue, Task]:
        item = cls(id=f"{cls.__name__.lower()}-1", title=cls.__name__)
        assert isinstance(item, Todo)
        assert item.status == WorkStatus.DRAFT


def test_supporting_objects_are_typed_and_linkable_by_id():
    requirement = Requirement(id="req-1", title="Must have provenance")
    criterion = AcceptanceCriterion(id="ac-1", title="Decision edge exists")
    blocker = Blocker(id="blocker-1", title="Missing Decision")
    decision = Decision(id="decision-1", title="Accept proposal")
    handoff = Handoff(id="handoff-1", title="Review handoff")
    packet = ReviewPacket(id="review-1", title="Review packet")
    milestone = Milestone(id="milestone-1", title="L3a")

    assert requirement.kind == "Requirement"
    assert criterion.kind == "AcceptanceCriterion"
    assert blocker.kind == "Blocker"
    assert decision.kind == "Decision"
    assert handoff.kind == "Handoff"
    assert packet.kind == "ReviewPacket"
    assert milestone.kind == "Milestone"


def test_forbidden_v0_classes_are_not_exported():
    for name in [
        "WorkRequest",
        "Case",
        "Subcase",
        "Deliverable",
        "Repository",
        "Release",
        "Commit",
        "LinearSync",
    ]:
        assert not hasattr(model, name), name


def test_work_object_construction_rejects_invalid_identity_and_numeric_values() -> None:
    with pytest.raises(InvalidWorkObjectId):
        Task(id="BAD", title="invalid id")
    with pytest.raises(NumericBoundError, match="invalid_version"):
        Task(id="task-1", title="invalid version", version=True)
    with pytest.raises(NumericBoundError, match="invalid_priority"):
        Task(id="task-1", title="invalid priority", priority=SIGNED_64_MAX + 1)


def test_work_object_construction_rejects_invalid_reference_ids() -> None:
    with pytest.raises(InvalidWorkObjectId):
        Task(id="task-1", title="invalid artifact", artifact_ids=("BAD_id",))
    with pytest.raises(InvalidWorkObjectId):
        Task(id="task-1", title="invalid link", external_link_ids=("bad space",))


def test_work_object_construction_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timestamps must be timezone-aware"):
        Task(id="task-1", title="naive time", created_at=datetime(2026, 1, 1))


def test_core_work_objects_can_reference_artifacts_and_external_links_by_id():
    proposal = Proposal(
        id="proposal-1",
        title="Artifact boundary proposal",
        artifact_ids=("artifact-1",),
        external_link_ids=("link-1",),
    )
    issue = Issue(
        id="issue-1",
        title="Implement artifact links",
        artifact_ids=("artifact-2",),
        external_link_ids=("link-2",),
    )
    task = Task(
        id="task-1",
        title="Verify fixtures",
        artifact_ids=("artifact-3",),
        external_link_ids=("link-3",),
    )

    for item in [proposal, issue, task]:
        props = item.to_node_properties()
        assert props["artifact_ids"] == list(item.artifact_ids)
        assert props["external_link_ids"] == list(item.external_link_ids)
        assert item.with_status(WorkStatus.REVIEW).artifact_ids == item.artifact_ids
        assert item.with_status(WorkStatus.REVIEW).external_link_ids == item.external_link_ids
