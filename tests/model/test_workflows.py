"""Workflow state is canonical, evidence guarded and independent of lifecycle."""

import json
from dataclasses import replace

import pytest
from tests.model.test_named_work import command, create, ref

from devgraph.kanban import BoardChanged, BoardFilter, build_board
from devgraph.model.base import WorkStatus
from devgraph.model.work import Issue
from devgraph.named_work import NamedWorkMutations
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.workflow_contract import LAYERS, STAGES, decode_state
from devgraph.workflows import WorkflowConflict, Workflows


class Scenario:
    def __init__(self, kind="Task"):
        self.storage = MemoryGraphStorage()
        self.service = NamedWorkMutations(self.storage)
        self.work = create(self.service, kind, "example")
        self.serial = 0

    def do(self, operation, **payload):
        self.work = self.service.execute(
            command(operation, self.work.kind, self.work.id, payload, self.work.version),
            actor_id="test-reviewer",
        )
        return self.work

    def move(self, stage, reason=""):
        return self.do("workflow.transition", stage=stage, reason=reason)

    def review(self, phase, *, complete=True, verdict="approved"):
        self.serial += 1
        evidence_id = f"evidence-{self.serial}"
        return self.do(
            "workflow.review",
            record_id=f"review-{self.serial}",
            phase=phase,
            verdict=verdict,
            summary="Reviewed at the object's boundary",
            evidence=[
                dict(
                    kind="ExternalLink",
                    id=evidence_id,
                    title="Test evidence",
                    url=f"https://example.test/evidence/{self.serial}",
                )
            ],
            requirements=[
                dict(
                    id="requirement-one",
                    title="Deliver the outcome",
                    outcome="met" if complete else "unmet",
                    evidence_ids=[evidence_id] if complete else [],
                    reason="",
                )
            ]
            if phase == "requirements"
            else [],
            layers=[dict(layer=layer, verdict="approved", reason="") for layer in LAYERS]
            if phase == "technical"
            else [],
        )


def test_execution_full_cycle_and_content_status_independence():
    s = Scenario()
    s.move("intake")
    with pytest.raises(WorkflowConflict):
        s.move("done")
    s.review("requirements", complete=False)
    s.move("requirements_normalized")
    s.review("baseline")
    s.move("baseline_captured")
    s.move("implementing_commit")
    s.review("commit")
    s.move("commit_ready_for_review")
    s.move("layer_review")
    s.review("technical")
    s.move("reconciling_ledger")
    with pytest.raises(WorkflowConflict):
        s.move("delivery_packet_ready")
    s.review("requirements")
    s.move("delivery_packet_ready")
    s.review("handoff")
    s.move("done")
    assert s.work.status == WorkStatus.DRAFT
    assert not Workflows(s.storage).transitions(s.work)
    with pytest.raises(WorkflowConflict):
        s.move("implementing_commit")


@pytest.mark.parametrize("kind", ["Initiative", "Project", "Proposal"])
def test_vibe_gates_and_proposal_provenance(kind):
    s = Scenario(kind)
    s.move("planning")
    with pytest.raises(WorkflowConflict, match="own workflow gate"):
        s.review("ceo_plan")
    s.review("requirements")
    s.move("developer_plan_approval")
    s.review("developer_plan")
    s.move("ceo_plan_approval")
    s.review("ceo_plan")
    s.move("ready")
    s.move("in_progress")
    s.move("ceo_delivery_approval")
    s.review("ceo_delivery")
    s.move("technical_review")
    s.review("technical")
    s.move("handoff_verification")
    s.review("handoff")
    if kind == "Proposal":
        with pytest.raises(WorkflowConflict, match="Decision provenance"):
            s.move("done")
        s.do("accept", decision_id="acceptance", decision_title="Approve proposed direction")
    s.move("done")
    record = s.storage.get_node("Handoff", f"review-{s.serial}")
    assert json.loads(record.properties["description"])["actor_id"] == "test-reviewer"


def test_waiting_requires_resolution_and_resumes_exact_interrupted_stage():
    s = Scenario()
    s.move("intake")
    with pytest.raises(WorkflowConflict):
        s.move("blocked_waiting_input")
    s.move("blocked_waiting_input", "Need a scope decision")
    with pytest.raises(WorkflowConflict):
        s.move("intake")
    with pytest.raises(WorkflowConflict):
        s.move("implementing_commit")
    s.review("resolution")
    s.move("intake")
    s.move("blocked_waiting_input", "A different decision")
    assert "resolution" not in decode_state(s.work.workflow_json).reviews


def test_stale_evidence_and_rework_do_not_reuse_approvals():
    s = Scenario("Project")
    s.move("planning")
    s.review("requirements")
    s.move("developer_plan_approval")
    s.review("developer_plan")
    s.do("patch", description="Scope changed")
    with pytest.raises(WorkflowConflict):
        s.move("ceo_plan_approval")
    s.move("planning", "Revise plan")
    assert decode_state(s.work.workflow_json).reviews == {}


def test_legacy_read_is_unset_and_conversion_starts_a_new_workflow():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    old = Issue(id="legacy", title="Older work", status=WorkStatus.ACCEPTED, workflow_json=None)
    service.repository.create(old)
    page = build_board(storage, BoardFilter())
    card = page["columns"][0]["items"][0]
    assert card["stage"] is None and card["stage_label"] == "Stage not set"
    assert storage.get_node("Issue", old.id).properties.get("workflow_json") is None
    assigned = service.execute(
        command(
            "workflow.assign",
            "Issue",
            old.id,
            dict(workflow_id="execution.v1", stage="intake", reason="Classify"),
            1,
        )
    )
    assert assigned.status == WorkStatus.ACCEPTED
    assert decode_state(assigned.workflow_json).stage == "intake"
    with pytest.raises(WorkflowConflict):
        service.execute(
            command(
                "workflow.assign",
                "Issue",
                old.id,
                dict(workflow_id="execution.v1", stage="done", reason="Skip"),
                2,
            )
        )
    s = Scenario("Proposal")
    s.do("accept", decision_id="accepted", decision_title="Accept direction")
    converted = s.do("convert", issue_id="new-issue", decision_id="accepted")
    assert decode_state(converted.workflow_json).stage == "backlog"
    assert decode_state(converted.workflow_json).workflow_id == "execution.v1"


@pytest.mark.parametrize(
    "parent,child",
    [
        ("Initiative", "Project"),
        ("Initiative", "Issue"),
        ("Initiative", "Task"),
        ("Project", "Issue"),
        ("Project", "Task"),
        ("Issue", "Task"),
    ],
)
def test_direct_parentage_scope_and_workflow_preservation(parent, child):
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    create(service, parent, "parent")
    original = create(service, child, "child")
    create(service, "Task", "standalone")
    changed = service.execute(
        command(
            "parent.set",
            child,
            "child",
            dict(previous_parent=None, parent=ref(parent, "parent", 1)),
            1,
        )
    )
    assert changed.workflow_json == original.workflow_json
    page = build_board(storage, BoardFilter(scope=f"{parent}/parent"))
    assert [x["id"] for c in page["columns"] for x in c["items"]] == ["child"]
    assert build_board(storage, BoardFilter())["total"] == 3


def test_pagination_counts_and_changed_cursor():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    for index in range(75):
        create(service, "Task", f"task-{index:03}")
    first = build_board(storage, BoardFilter(limit=30))
    assert first["total"] == first["columns"][0]["count"] == 75
    assert len(first["columns"][0]["items"]) == 30
    second = build_board(
        storage,
        BoardFilter(
            limit=30,
            column=("backlog",),
            after=first["columns"][0]["next_cursor"],
            revision=first["revision"],
        ),
    )
    assert second["columns"][0]["items"][0]["id"] == "task-030"
    create(service, "Issue", "new")
    with pytest.raises(BoardChanged):
        build_board(storage, BoardFilter(revision=first["revision"]))


def test_failed_review_rolls_back_evidence_and_subject():
    s = Scenario()
    before = s.storage.query()
    with pytest.raises(WorkflowConflict):
        s.review("handoff")
    assert s.storage.query() == before


def test_every_stage_has_an_explicit_column_and_done_has_no_forward_edge():
    for workflow, stages in STAGES.items():
        for stage, _, _ in stages:
            if stage == "blocked_waiting_input":
                continue
            s = Scenario("Task" if workflow == "execution.v1" else "Project")
            state = decode_state(s.work.workflow_json)
            state.stage = stage
            from devgraph.workflow_contract import encode_state

            work = replace(s.work, workflow_json=encode_state(state))
            transitions = Workflows(s.storage).transitions(work)
            assert bool(transitions) == (stage != "done")


def test_external_evidence_edit_invalidates_an_approved_gate():
    s = Scenario("Project")
    s.move("planning")
    s.review("requirements")
    s.move("developer_plan_approval")
    s.review("developer_plan")
    assert not Workflows(s.storage).gaps(s.work, decode_state(s.work.workflow_json),
                                         "ceo_plan_approval")
    node = s.storage.get_node("ExternalLink", "evidence-2")
    s.storage.update_node("ExternalLink", node.id, {**node.properties, "title": "Changed"})
    with pytest.raises(WorkflowConflict):
        s.move("ceo_plan_approval")


def test_completed_child_does_not_approve_parent_and_workflow_survives_writes():
    from devgraph.workflow_contract import encode_state
    s = Scenario("Project")
    child = create(s.service, "Task", "child")
    state = decode_state(child.workflow_json)
    state.stage = "done"  # Completed fixture; parent's approval still has its own boundary.
    completed = replace(child, workflow_json=encode_state(state), version=child.version + 1)
    s.storage.update_node(child.kind, child.id, completed.to_node_properties())
    s.storage.create_edge("Project", s.work.id, "HAS_CHILD", "Task", child.id)
    s.move("planning")
    s.review("requirements")
    s.move("developer_plan_approval")
    assert Workflows(s.storage).detail(s.work)["child_progress"] == {"done": 1, "total": 1}
    assert Workflows(s.storage).detail(s.work)["child_subjects"] == []
    with pytest.raises(WorkflowConflict):
        s.move("ceo_plan_approval")
    original = s.work.workflow_json
    s.do("patch", title="Revised title")
    assert s.work.workflow_json == original
    s.do("status", status="review")
    assert s.work.workflow_json == original
    s.do("archive")
    assert s.work.workflow_json == original


@pytest.mark.parametrize("parent,child", [("Initiative", "Issue"), ("Initiative", "Task"),
                                         ("Project", "Task")])
def test_flattened_parentage_inherits_root_arena(parent, child):
    from tests.model.test_arena_runtime import add_arena, assign

    from devgraph.arenas import ArenaMutations
    storage = MemoryGraphStorage()
    work = NamedWorkMutations(storage)
    arena = ArenaMutations(storage)
    add_arena(arena)
    create(work, "Initiative", "root")
    if parent == "Project":
        create(work, parent, "parent")
        storage.create_edge("Initiative", "root", "HAS_CHILD", parent, "parent")
    create(work, child, "child")
    storage.create_edge(parent, "root" if parent == "Initiative" else "parent",
                        "HAS_CHILD", child, "child")
    assign(arena, "Initiative", "root", 1, arena=ref("Arena", "gallery", 1))
    membership = arena.repository.membership(child, "child")
    assert membership.inherited and membership.arena.id == "gallery"
    scoped = build_board(storage, BoardFilter(scope="Initiative/root", descendants=True))
    assert "child" in [c["id"] for column in scoped["columns"] for c in column["items"]]
