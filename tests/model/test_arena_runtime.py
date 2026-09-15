"""Direct membership belongs only to eligible Work roots."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.model.test_named_work import command, create, ref

from devgraph.arena_requests import ArenaRequest, InvalidArenaRequest
from devgraph.arenas import ArenaConflict, ArenaMutations, ArenaRepository
from devgraph.model.repository import WorkObjectVersionConflictError
from devgraph.named_work import NamedWorkMutations
from devgraph.storage.memory import MemoryGraphStorage


def test_shared_arena_canonical_and_negative_vectors():
    from devgraph.named_requests import parse_named_request

    root = Path(__file__).parents[1] / "fixtures/arena-v1"
    for vector in json.loads((root / "requests.json").read_text()):
        parsed = parse_named_request(vector["raw"].encode())
        assert parsed.canonical.decode() == vector["canonical"]
        assert parsed.digest == vector["digest"]
        assert list(parsed.resources) == vector["resources"]
    for vector in json.loads((root / "invalid-requests.json").read_text()):
        with pytest.raises(InvalidArenaRequest):
            ArenaRequest.from_json(vector["raw"].encode())


def arena_command(operation, kind="Arena", subject="gallery", payload=None, version=None):
    return ArenaRequest.from_json(json.dumps({
        "schema": "devgraph.arena-request.v1", "operation": operation,
        "kind": kind, "id": subject, "expected_version": version, "payload": payload or {},
    }).encode())


def add_arena(service, arena_id="gallery"):
    return service.execute(arena_command("create", subject=arena_id, payload={
        "id": arena_id, "title": arena_id.title(),
    }))


def assign(service, kind, subject, version, previous=None, arena=None):
    return service.execute(arena_command("member.set", kind, subject, {
        "previous_arena": previous, "arena": arena,
    }, version))


def test_arena_create_patch_archive_preserves_separate_record_shape():
    storage = MemoryGraphStorage()
    service = ArenaMutations(storage)
    arena = add_arena(service)
    assert arena.kind == "Arena" and arena.version == 1 and not arena.archived
    assert not hasattr(arena, "priority") and not hasattr(arena, "status")
    updated = service.execute(arena_command("patch", payload={"description": "Gallery work"},
                                           version=1))
    assert updated.description == "Gallery work" and updated.version == 2
    with pytest.raises(WorkObjectVersionConflictError):
        service.execute(arena_command("archive", version=1))
    archived = service.execute(arena_command("archive", version=2))
    assert archived.archived and archived.version == 3
    assert service.repository.list() == []
    assert service.repository.list(include_archived=True) == [archived]


def test_initiative_and_standalone_task_membership_and_inheritance():
    storage = MemoryGraphStorage()
    work = NamedWorkMutations(storage)
    service = ArenaMutations(storage)
    add_arena(service)
    for kind, name in [("Initiative", "gallery-roadmap"), ("Project", "images"),
                       ("Issue", "sharing"), ("Task", "implement"), ("Task", "review")]:
        create(work, kind, name)
    storage.create_edge("Initiative", "gallery-roadmap", "HAS_CHILD", "Project", "images")
    storage.create_edge("Project", "images", "HAS_CHILD", "Issue", "sharing")
    storage.create_edge("Issue", "sharing", "HAS_CHILD", "Task", "implement")
    assign(service, "Initiative", "gallery-roadmap", 1, arena=ref("Arena", "gallery", 1))
    assign(service, "Task", "review", 1, arena=ref("Arena", "gallery", 2))
    resolved = service.repository.membership("Task", "implement")
    assert resolved.arena.id == "gallery" and resolved.inherited
    assert (resolved.root_kind, resolved.root_id) == ("Initiative", "gallery-roadmap")
    direct = service.repository.membership("Task", "review")
    assert direct.arena.id == "gallery" and not direct.inherited
    assert [(item.kind, item.id) for item in service.repository.members("gallery")] == [
        ("Initiative", "gallery-roadmap"), ("Task", "review"),
    ]
    assert len(storage.list_edges("CONTAINS_WORK")) == 2
    assert work.repository.get_by_id("Task", "implement").version == 1


def test_parentless_is_graph_derived_and_archived_parent_still_counts():
    storage = MemoryGraphStorage()
    work = NamedWorkMutations(storage)
    service = ArenaMutations(storage)
    add_arena(service)
    create(work, "Task", "child")
    create(work, "Issue", "parent")
    storage.create_edge("Issue", "parent", "HAS_CHILD", "Task", "child")
    work.execute(command("archive", "Issue", "parent", version=1))
    with pytest.raises(ArenaConflict):
        assign(service, "Task", "child", 1, arena=ref("Arena", "gallery", 1))
    assert storage.list_edges("CONTAINS_WORK") == []


def test_atomic_move_and_parent_assignment_bind_all_changed_resources():
    storage = MemoryGraphStorage()
    work = NamedWorkMutations(storage)
    service = ArenaMutations(storage)
    add_arena(service)
    add_arena(service, "research")
    create(work, "Task", "review")
    create(work, "Issue", "issue")
    assign(service, "Task", "review", 1, arena=ref("Arena", "gallery", 1))
    with pytest.raises(ArenaConflict):
        assign(service, "Task", "review", 2, arena=ref("Arena", "research", 1))
    moved = assign(service, "Task", "review", 2, previous=ref("Arena", "gallery", 2),
                   arena=ref("Arena", "research", 1))
    assert moved.version == 3
    parent_payload = {"previous_parent": None, "parent": ref("Issue", "issue", 1)}
    with pytest.raises(ArenaConflict):
        work.execute(command("parent.set", "Task", "review", parent_payload, 3))
    parent_payload["previous_arena"] = ref("Arena", "research", 2)
    request = command("parent.set", "Task", "review", parent_payload, 3)
    assert "Arena/research" in request.resources
    assert work.execute(request).version == 4
    assert storage.list_edges("CONTAINS_WORK") == []
    assert service.repository.membership("Task", "review").arena is None
    assert service.repository.get("research").version == 3


def test_archive_retains_membership_and_allows_explicit_removal():
    storage = MemoryGraphStorage()
    work = NamedWorkMutations(storage)
    service = ArenaMutations(storage)
    add_arena(service)
    create(work, "Task", "review")
    assign(service, "Task", "review", 1, arena=ref("Arena", "gallery", 1))
    service.execute(arena_command("archive", version=2))
    assert service.repository.membership("Task", "review").arena.archived
    assert not work.repository.get_by_id("Task", "review").status.value == "archived"
    assign(service, "Task", "review", 2, previous=ref("Arena", "gallery", 3))
    assert service.repository.get("gallery").archived
    assert service.repository.membership("Task", "review").arena is None


def test_concurrent_assignment_has_one_winner():
    storage = MemoryGraphStorage()
    work, service = NamedWorkMutations(storage), ArenaMutations(storage)
    add_arena(service)
    add_arena(service, "research")
    create(work, "Task", "review")

    def attempt(arena):
        try:
            assign(service, "Task", "review", 1, arena=ref("Arena", arena, 1))
            return "assigned"
        except WorkObjectVersionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["gallery", "research"])) == ["assigned", "conflict"]
    assert len(storage.list_edges("CONTAINS_WORK")) == 1


@pytest.mark.parametrize("corruption", ["parents", "cycle", "arenas", "duplicate"])
def test_ambiguous_or_cyclic_membership_fails_closed(corruption):
    storage = MemoryGraphStorage()
    work, service = NamedWorkMutations(storage), ArenaMutations(storage)
    add_arena(service)
    add_arena(service, "research")
    for kind, name in [("Initiative", "root"), ("Project", "p"), ("Project", "q")]:
        create(work, kind, name)
    storage.create_edge("Arena", "gallery", "CONTAINS_WORK", "Initiative", "root")
    storage.create_edge("Initiative", "root", "HAS_CHILD", "Project", "p")
    if corruption == "parents":
        storage.create_edge("Project", "q", "HAS_CHILD", "Project", "p")
    elif corruption == "cycle":
        storage.create_edge("Project", "p", "HAS_CHILD", "Initiative", "root")
    elif corruption == "arenas":
        storage.create_edge("Arena", "research", "CONTAINS_WORK", "Initiative", "root")
    else:
        storage._edges.append(storage.list_edges("CONTAINS_WORK")[0])
    with pytest.raises(ArenaConflict):
        service.repository.membership("Project", "p")


@pytest.mark.parametrize("kind", ["Project", "Issue", "Proposal", "Todo", "Arena"])
def test_nonmember_kinds_rejected_by_request_contract(kind):
    with pytest.raises(InvalidArenaRequest):
        arena_command("member.set", kind, "w", {
            "previous_arena": None, "arena": ref("Arena", "gallery", 1),
        }, 1)


def test_membership_and_arena_list_pages_are_bounded():
    storage = MemoryGraphStorage()
    work, service = NamedWorkMutations(storage), ArenaMutations(storage)
    add_arena(service)
    add_arena(service, "research")
    for name in ("a", "b", "c"):
        create(work, "Task", name)
        arena = service.repository.get("gallery")
        assign(service, "Task", name, 1, arena=ref("Arena", "gallery", arena.version))
    assert [a.id for a in service.repository.list(after_id="gallery", limit=1)] == ["research"]
    assert [w.id for w in service.repository.members("gallery", after_resource="Task/a",
                                                    limit=1)] == ["b"]
    with pytest.raises(ValueError):
        service.repository.members("gallery", limit=101)
    with pytest.raises(ValueError):
        service.repository.members("gallery", after_resource="Arena/gallery")


def test_membership_failure_rolls_back_edges_and_versions():
    class BrokenStorage(MemoryGraphStorage):
        def create_edge(self, *args, **kwargs):
            super().create_edge(*args, **kwargs)
            raise RuntimeError("synthetic failure")

    storage = BrokenStorage()
    work, service = NamedWorkMutations(storage), ArenaMutations(storage)
    add_arena(service)
    create(work, "Task", "review")
    with pytest.raises(RuntimeError):
        assign(service, "Task", "review", 1, arena=ref("Arena", "gallery", 1))
    assert storage.list_edges("CONTAINS_WORK") == []
    assert ArenaRepository(storage).get("gallery").version == 1
    assert work.repository.get_by_id("Task", "review").version == 1
