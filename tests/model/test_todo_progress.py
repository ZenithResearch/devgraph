"""Shared progress is independent of archive, containment and legacy status."""

import json
from dataclasses import replace
from urllib.parse import urlencode

import pytest

from devgraph.kanban import BoardChanged, BoardFilter, build_board
from devgraph.model.base import WorkStatus
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project, Proposal, Requirement, Task, Todo
from devgraph.named_work import NamedWorkMutations
from devgraph.progress import Progress, stage_progress
from devgraph.progress_migration import migration_report, plan_progress_migration
from devgraph.storage.base import NodeRecord
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.todo_views import TodoFilters, todo_page
from devgraph.work_requests import InvalidWorkRequest, WorkRequest
from devgraph.workflow_contract import STAGES, WorkflowState, encode_state
from devgraph.workflows import WorkflowConflict, Workflows


def command(work, operation, **payload):
    return WorkRequest.from_json(
        json.dumps(
            dict(
                schema="devgraph.work-request.v2",
                operation=operation,
                kind=work.kind,
                id=work.id,
                expected_version=work.version,
                payload=payload,
            )
        ).encode()
    )


def progress(service, work, target):
    return service.execute(
        command(
            work,
            "progress.set",
            progress=target,
            reason="Owner attestation",
            record_id=f"progress-{work.id}-{work.version}",
        ),
        actor_id="test-owner",
    )


def test_simple_todo_needs_no_workflow_and_records_completion_and_rework():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    work = service.repository.create(Todo(id="simple", title="Call the supplier"))
    assert work.progress == Progress.NOT_STARTED and work.workflow_json is None
    work = progress(service, work, "done")
    assert work.progress == Progress.DONE and work.workflow_json is None
    record = storage.get_node("ReviewPacket", work.progress_record_id)
    assert json.loads(record.properties["description"])["actor_id"] == "test-owner"
    with pytest.raises(ValueError, match="Reopen"):
        service.execute(command(work, "patch", description="Changed scope"))
    work = progress(service, work, "in_progress")
    with pytest.raises(WorkflowConflict, match="Rework"):
        progress(service, work, "not_started")
    work = service.execute(command(work, "patch", description="Changed scope"))
    assert work.progress == Progress.IN_PROGRESS


@pytest.mark.parametrize("model", [Todo, Proposal, Initiative, Project, Issue, Task])
@pytest.mark.parametrize("state", list(Progress))
def test_archive_and_restore_preserve_progress_stage_and_history(model, state):
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    work = model(id="one", title="Archive me")
    workflow = None
    if work.workflow_json:
        from devgraph.workflow_contract import decode_state

        flow = decode_state(work.workflow_json)
        flow.stage = {
            "not_started": "backlog",
            "in_progress": "planning" if flow.workflow_id == "vibe-ceo.v1" else "intake",
            "done": "done",
        }[state]
        workflow = encode_state(flow)
    work = service.repository.create(replace(work, progress=state, workflow_json=workflow))
    archived = service.execute(command(work, "archive"))
    assert archived.archived and archived.progress == state and archived.workflow_json == workflow
    assert archived.status == work.status
    restored = service.execute(command(archived, "restore"))
    assert (
        not restored.archived and restored.progress == state and restored.workflow_json == workflow
    )
    assert restored.version == work.version + 2
    assert service.repository.get_by_id(work.kind, work.id) == restored


def test_central_stage_mapping_and_contradiction_rejection():
    for workflow, rows in STAGES.items():
        for stage, _, _ in rows:
            expected = (
                "not_started"
                if stage == "backlog"
                else "done"
                if stage == "done"
                else "in_progress"
            )
            assert stage_progress(workflow, stage) == expected
    with pytest.raises(ValueError, match="progress_stage_conflict"):
        Task(id="bad", title="Contradiction", progress="done")


def test_subtype_cannot_skip_completion_and_started_cannot_be_reassigned_to_backlog():
    service = NamedWorkMutations(MemoryGraphStorage())
    work = service.repository.create(Project(id="project", title="Plan"))
    with pytest.raises(WorkflowConflict):
        progress(service, work, "done")
    work = service.execute(command(work, "workflow.transition", stage="planning"))
    assert work.progress == "in_progress"
    work = service.execute(
        command(work, "workflow.transition", stage="blocked_waiting_input", reason="Need input")
    )
    assert work.progress == "in_progress"
    with pytest.raises(WorkflowConflict):
        service.execute(command(work, "workflow.transition", stage="planning"))


def test_base_todo_attached_requirements_need_evidence():
    service = NamedWorkMutations(MemoryGraphStorage())
    work = service.repository.create(Todo(id="todo", title="Outcome"))
    requirement = service.repository.create(Requirement(id="requirement", title="Requirement"))
    service.storage.create_edge(
        work.kind, work.id, "HAS_REQUIREMENT", requirement.kind, requirement.id
    )
    assert Workflows(service.storage).requirements(work)
    with pytest.raises(WorkflowConflict):
        progress(service, work, "done")
    assert service.repository.get_by_id("Todo", "todo").progress == "not_started"


def legacy(storage, work):
    props = work.to_node_properties()
    for key in ("progress", "progress_schema", "workflow_json"):
        props.pop(key, None)
    storage._nodes[(work.kind, work.id)] = NodeRecord(work.kind, work.id, props, work.archived)


def test_migration_never_infers_draft_or_accepted_and_checks_explicit_done():
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    legacy(storage, Task(id="draft", title="Old Draft"))
    legacy(storage, Issue(id="accepted", title="Old Accepted", status=WorkStatus.ACCEPTED))
    legacy(
        storage,
        Todo(id="archived", title="Old Archived", status=WorkStatus.ARCHIVED, archived=True),
    )
    repo.create(Task(id="backlog", title="Recorded Backlog"))
    repo.create(
        Project(
            id="planning",
            title="Recorded Planning",
            progress="in_progress",
            workflow_json=encode_state(WorkflowState(workflow_id="vibe-ceo.v1", stage="planning")),
        )
    )
    repo.create(
        Task(
            id="false-done",
            title="Missing gates",
            progress="done",
            workflow_json=encode_state(WorkflowState(workflow_id="execution.v1", stage="done")),
        )
    )
    plan = {row["id"]: row for row in plan_progress_migration(storage)}
    assert plan["backlog"]["progress"] == "not_started"
    assert plan["planning"]["progress"] == "in_progress"
    for key in ("draft", "accepted", "archived", "false-done"):
        assert plan[key]["progress"] is None and plan[key]["reasons"]
    assert plan["archived"]["archived"]
    report = migration_report(list(plan.values()))
    assert report["mapped"] == {"not_started": 1, "in_progress": 1, "done": 0}
    assert len(report["unresolved"]) == 4
    assert repo.get_by_id("Task", "draft").progress is None
    assert repo.get_by_id("Issue", "accepted").progress is None


@pytest.mark.parametrize("size", [250, 1500])
def test_daily_counts_priority_pages_and_unclassified_are_independent(size):
    storage = MemoryGraphStorage()
    repo = WorkObjectRepository(storage)
    models = [Todo, Proposal, Initiative, Project, Issue, Task]
    for i in range(size):
        repo.create(models[i % 6](id=f"item-{i:04}", title="Work", priority=i % 5))
    legacy(storage, Task(id="old", title="Unclassified"))
    archived = repo.archive("Todo", "item-0000")
    assert archived.progress == "not_started"
    filters = "progress=not_started&archived=exclude&limit=100"
    page = todo_page(storage, TodoFilters.parse(filters))
    seen = []
    priorities = []
    assert page["total"] == size - 1 and page["classification_required"] == 1
    while True:
        seen.extend(x["key"] for x in page["items"])
        priorities.extend(int(x["priority"]) for x in page["items"])
        assert len(page["items"]) <= 100 and page["total"] == size - 1
        if not page["next_cursor"]:
            break
        page = todo_page(
            storage,
            TodoFilters.parse(
                filters
                + "&"
                + urlencode({"after": page["next_cursor"], "revision": page["revision"]})
            ),
        )
    assert len(seen) == len(set(seen)) == size - 1
    assert priorities == sorted(priorities, reverse=True)
    board = build_board(storage, BoardFilter(), version=2)
    assert next(c for c in board["columns"] if c["id"] == "unclassified")["count"] == 1
    repo.archive("Proposal", "item-0001")
    with pytest.raises(BoardChanged):
        todo_page(
            storage,
            TodoFilters.parse(
                filters + "&" + urlencode({"after": seen[0], "revision": page["revision"]})
            ),
        )


def test_new_domain_does_not_change_legacy_request_bytes_or_admit_new_ops_in_v1():
    raw = dict(
        schema="devgraph.work-request.v2",
        operation="create",
        kind="Todo",
        id="one",
        expected_version=None,
        payload={"id": "one", "title": "Todo"},
    )
    request = WorkRequest.from_json(json.dumps(raw).encode())
    assert request.authority_operation == "devgraph.work.create.v2"
    raw["schema"] = "devgraph.work-request.v1"
    with pytest.raises(InvalidWorkRequest):
        WorkRequest.from_json(json.dumps(raw).encode())


def test_base_completion_evidence_and_rejected_proposal_disposition_survive_migration():
    service = NamedWorkMutations(MemoryGraphStorage())
    todo = service.repository.create(Todo(id="simple", title="A simple todo"))
    todo = progress(service, todo, "done")
    proposal = service.repository.create(Proposal(id="proposal", title="An idea"))
    delivery = service.repository.create(Project(id="delivery", title="Independent delivery"))
    proposal = service.execute(
        command(proposal, "proposal.reject", decision_id="reject", reason="Out of scope")
    )
    proposal = service.execute(
        command(
            proposal,
            "progress.set",
            progress="done",
            reason="Disposition communicated",
            record_id="disposition",
            evidence=[
                dict(
                    kind="ExternalLink",
                    id="note",
                    title="Notice",
                    url="https://example.test/decision",
                )
            ],
        ),
        actor_id="reviewer",
    )
    plan = {row["key"]: row for row in plan_progress_migration(service.storage)}
    assert plan["Todo/simple"]["progress"] == "done"
    assert plan["Proposal/proposal"]["progress"] == "done"
    assert service.repository.get_by_id(delivery.kind, delivery.id).progress == "not_started"
    service.storage.archive_node("ExternalLink", "note")
    plan = {row["key"]: row for row in plan_progress_migration(service.storage)}
    assert plan["Proposal/proposal"]["progress"] is None
    assert plan["Proposal/proposal"]["reasons"]


def test_base_completion_validates_attached_ledger_and_current_evidence():
    service = NamedWorkMutations(MemoryGraphStorage())
    todo = service.repository.create(Todo(id="todo", title="Outcome"))
    req = service.repository.create(Requirement(id="req", title="Checklist"))
    service.storage.create_edge("Todo", todo.id, "HAS_REQUIREMENT", req.kind, req.id)
    todo = service.execute(
        command(
            todo,
            "progress.set",
            progress="done",
            reason="Checked",
            record_id="complete",
            evidence=[
                dict(
                    kind="ExternalLink",
                    id="evidence",
                    title="Proof",
                    url="https://example.test/check",
                )
            ],
            requirements=[
                dict(
                    id="req",
                    subject="Requirement/req",
                    title="Checklist",
                    outcome="met",
                    evidence_ids=["evidence"],
                    reason="",
                )
            ],
        ),
        actor_id="owner",
    )
    assert plan_progress_migration(service.storage)[0]["progress"] == "done"
    service.repository.update(
        replace(req, description="Scope changed"), expected_version=req.version
    )
    assert plan_progress_migration(service.storage)[0]["progress"] is None


def test_restored_historical_archived_status_does_not_rearchive_on_edit():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    work = Todo(
        id="old", title="Historical", status=WorkStatus.ARCHIVED, archived=True, progress=None
    )
    storage._nodes[(work.kind, work.id)] = NodeRecord(
        work.kind, work.id, work.to_node_properties(), True
    )
    work = service.execute(command(work, "restore"))
    work = service.execute(command(work, "patch", title="Restored and edited"))
    assert work.status == WorkStatus.ARCHIVED and not work.archived and work.progress is None
    work = progress(service, work, "not_started")
    assert (
        not work.archived and todo_page(storage, TodoFilters(progress="not_started"))["total"] == 1
    )
