from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from devgraph.model.base import WorkStatus
from devgraph.model.repository import WorkObjectVersionConflictError
from devgraph.named_work import NamedWorkMutations, WorkRelationshipConflict
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.work_requests import WorkRequest


def command(operation, kind, work_id, payload=None, version=None):
    return WorkRequest.from_json(
        json.dumps(
            {
                "schema": "devgraph.work-request.v1",
                "operation": operation,
                "kind": kind,
                "id": work_id,
                "expected_version": version,
                "payload": payload or {},
            }
        ).encode()
    )


def create(service, kind, work_id):
    return service.execute(command("create", kind, work_id, {"id": work_id, "title": "Fixture"}))


def ref(kind, work_id, version):
    return {"kind": kind, "id": work_id, "expected_version": version}


@pytest.mark.parametrize("kind", ["Proposal", "Initiative", "Project", "Issue", "Task"])
def test_named_content_status_and_archive_lifecycle(kind):
    service = NamedWorkMutations(MemoryGraphStorage())
    assert create(service, kind, "w-1").version == 1
    patched = service.execute(command("patch", kind, "w-1", {"title": "Updated"}, 1))
    assert patched.title == "Updated" and patched.version == 2
    reviewed = service.execute(command("status", kind, "w-1", {"status": "review"}, 2))
    assert reviewed.status == WorkStatus.REVIEW and reviewed.version == 3
    with pytest.raises(WorkObjectVersionConflictError):
        service.execute(command("archive", kind, "w-1", version=2))
    archived = service.execute(command("archive", kind, "w-1", version=3))
    assert archived.status == WorkStatus.ARCHIVED and archived.version == 4


def test_parent_attach_reparent_detach_and_stale_precondition():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    for kind, name in [("Project", "old"), ("Project", "new"), ("Issue", "child")]:
        create(service, kind, name)
    attached = service.execute(
        command(
            "parent.set",
            "Issue",
            "child",
            {
                "previous_parent": None,
                "parent": ref("Project", "old", 1),
            },
            1,
        )
    )
    assert attached.version == 2
    reparent = command(
        "parent.set",
        "Issue",
        "child",
        {
            "previous_parent": ref("Project", "old", 2),
            "parent": ref("Project", "new", 1),
        },
        2,
    )
    assert service.execute(reparent).version == 3
    with pytest.raises(WorkObjectVersionConflictError):
        service.execute(reparent)
    assert [(edge.from_id, edge.to_id) for edge in storage.list_edges("HAS_CHILD")] == [
        ("new", "child")
    ]
    service.execute(
        command(
            "parent.set",
            "Issue",
            "child",
            {
                "previous_parent": ref("Project", "new", 2),
                "parent": None,
            },
            3,
        )
    )
    assert storage.list_edges("HAS_CHILD") == []


@pytest.mark.parametrize(
    "prefix,relationship", [("dependency", "DEPENDS_ON"), ("blocker", "BLOCKS")]
)
def test_edge_direction_cycle_denial_and_removal(prefix, relationship):
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    create(service, "Task", "a")
    create(service, "Task", "b")
    service.execute(command(f"{prefix}.add", "Task", "a", {"target": ref("Task", "b", 1)}, 1))
    assert [(edge.from_id, edge.to_id) for edge in storage.list_edges(relationship)] == [("a", "b")]
    with pytest.raises(WorkRelationshipConflict, match="cycle"):
        service.execute(command(f"{prefix}.add", "Task", "b", {"target": ref("Task", "a", 2)}, 2))
    assert service.repository.get_by_id("Task", "a").version == 2
    service.execute(command(f"{prefix}.remove", "Task", "a", {"target": ref("Task", "b", 2)}, 2))
    assert storage.list_edges(relationship) == []


def test_concurrent_opposite_dependencies_have_one_winner():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    create(service, "Task", "a")
    create(service, "Task", "b")

    def attempt(source, target):
        try:
            service.execute(
                command("dependency.add", "Task", source, {"target": ref("Task", target, 1)}, 1)
            )
            return "accepted"
        except WorkObjectVersionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, "a", "b"), pool.submit(attempt, "b", "a")]
        assert sorted(f.result() for f in futures) == ["accepted", "conflict"]
    assert len(storage.list_edges("DEPENDS_ON")) == 1


def test_proposal_acceptance_and_conversion_preserve_decision_provenance():
    storage = MemoryGraphStorage()
    service = NamedWorkMutations(storage)
    create(service, "Proposal", "p")
    accepted = service.execute(
        command(
            "accept",
            "Proposal",
            "p",
            {
                "decision_id": "d",
                "decision_title": "Accept fixture",
            },
            1,
        )
    )
    assert accepted.status == WorkStatus.ACCEPTED
    issue = service.execute(
        command("convert", "Proposal", "p", {"issue_id": "i", "decision_id": "d"}, 2)
    )
    assert issue.kind == "Issue" and issue.status == WorkStatus.DRAFT
    assert service.repository.get_by_id("Proposal", "p").version == 3
    assert len(storage.list_edges("CONVERSION_DECIDED_BY")) == 1


def test_cancellation_rolls_back_relationship_and_versions():
    class CancelAfterEdge(MemoryGraphStorage):
        def create_edge(self, *args, **kwargs):
            super().create_edge(*args, **kwargs)
            raise KeyboardInterrupt("synthetic cancellation")

    storage = CancelAfterEdge()
    service = NamedWorkMutations(storage)
    create(service, "Task", "a")
    create(service, "Task", "b")
    with pytest.raises(KeyboardInterrupt):
        service.execute(command("dependency.add", "Task", "a", {"target": ref("Task", "b", 1)}, 1))
    assert storage.list_edges() == []
    assert service.repository.get_by_id("Task", "a").version == 1


def test_derived_routing_fields_are_not_authority():
    service = NamedWorkMutations(MemoryGraphStorage())
    original = command("create", "Issue", "i", {"id": "i", "title": "Fixture"})
    forged = replace(original, operation="archive", kind="Task", id="t")
    created = service.execute(forged)
    assert (created.kind, created.id, created.version) == ("Issue", "i", 1)
