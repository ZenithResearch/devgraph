"""Monitor placement is authorized independently of visible hierarchy edges."""

import json
from unittest.mock import patch

import pytest
from tests.model.test_arena_runtime import add_arena

from devgraph.arenas import ArenaMutations
from devgraph.auth.arena_access import CollectionArenaReadGrant, VerifiedArenaReadGrant
from devgraph.auth.errors import ForbiddenError
from devgraph.auth.monitor_read import MonitorWorkGrant, project_monitor_read
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Project, Task
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage


def fixture():
    storage = MemoryGraphStorage()
    for name in ("shared", "other"):
        add_arena(ArenaMutations(storage), name)
    repo = WorkObjectRepository(storage)
    repo.create(Initiative(id="hidden", title="Secret ancestor"))
    repo.create(Project(id="project", title="Project"))
    repo.create(Task(id="task", title="Task"))
    storage.create_edge("Arena", "shared", "CONTAINS_WORK", "Initiative", "hidden")
    storage.create_edge("Initiative", "hidden", "HAS_CHILD", "Project", "project")
    storage.create_edge("Project", "project", "HAS_CHILD", "Task", "task")
    work = MonitorWorkGrant(
        scope="devgraph.work.read",
        operations=["list"],
        relationship_types=["parent"],
        work_kinds=["Project", "Task"],
        include_archived=False,
    )
    arenas = VerifiedArenaReadGrant(
        scope="devgraph.arena.read", arena_ids=["shared"], operations=["list"]
    )
    return storage, work, arenas


def test_hidden_and_archived_parents_do_not_erase_authorized_arena_placement():
    storage, work, arenas = fixture()
    from tests.model.test_named_work import command

    from devgraph.named_work import NamedWorkMutations

    NamedWorkMutations(storage).execute(command("archive", "Initiative", "hidden", version=1))
    with patch.object(
        storage, "work_containment_batch", wraps=storage.work_containment_batch
    ) as batch:
        result = project_monitor_read(
            storage,
            [work, arenas],
            "/monitor/topology/v1?arena=Arena%3Ashared&category=work&work_kind=Task&relationship=BLOCKS",
        )
    assert batch.call_count == 2, "one visible frontier plus one shared hidden ancestor"
    assert [(n["id"], n["arena_key"]) for n in result["graph_nodes"]] == [("task", "Arena:shared")]
    assert {n["key"] for n in result["layout_context"]["nodes"]} == {"Task:task", "Arena:shared"}
    assert result["graph_edges"] == result["layout_context"]["edges"] == []
    assert result["access_scope"]["arena_membership_complete"] is True
    text = json.dumps(result)
    assert "Secret ancestor" not in text and "Initiative:hidden" not in text
    assert "Arena:other" not in text


@pytest.mark.parametrize(
    "path", ["/monitor/topology/v1", "/monitor/snapshot", "/monitor/kanban/v2"]
)
def test_monitor_requires_list_authority_covering_actual_arenas(path):
    storage, work, arenas = fixture()
    for denied in (
        [],
        [arenas.model_copy(update={"operations": ("get",)})],
        [arenas.model_copy(update={"arena_ids": ("other",)})],
    ):
        with pytest.raises(ForbiddenError, match="monitor_arena_context_required"):
            project_monitor_read(storage, [work, *denied], path)
    # The same Work-only credential remains sufficient for other applications.
    assert len(project_monitor_read(storage, [work], "/todos/v2")["items"]) == 2


def test_arena_move_invalidates_topology_without_exposing_or_reparenting_work():
    storage, work, arenas = fixture()
    arenas = CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True)
    full = project_monitor_read(storage, [work, arenas], "/monitor/topology/v1")
    path = "/monitor/topology/v1?if_revision=" + full["representation_revision"]
    storage.delete_edge("Arena", "shared", "CONTAINS_WORK", "Initiative", "hidden")
    storage.create_edge("Arena", "other", "CONTAINS_WORK", "Initiative", "hidden")
    moved = project_monitor_read(storage, [work, arenas], path)
    assert moved["schema"] == "devgraph.topology.v1"
    assert moved["revision"] != full["revision"]
    assert {n["arena_key"] for n in moved["graph_nodes"] if n["category"] == "work"} == {
        "Arena:other"
    }
    assert moved["graph_edges"] == []


def test_malformed_parentage_is_not_reported_as_unassigned():
    storage, work, arenas = fixture()
    storage.create_edge("Task", "task", "HAS_CHILD", "Project", "project")
    with pytest.raises(StorageUnavailable, match="invalid Arena placement"):
        project_monitor_read(storage, [work, arenas], "/monitor/topology/v1")


def test_archived_arena_remains_authorized_placement_context():
    from tests.model.test_arena_runtime import arena_command

    storage, work, arenas = fixture()
    ArenaMutations(storage).execute(arena_command("archive", subject="shared", version=1))
    result = project_monitor_read(storage, [work, arenas], "/monitor/topology/v1")
    assert {n["arena_key"] for n in result["graph_nodes"] if n["category"] == "work"} == {
        "Arena:shared"
    }
    assert result["arenas"][0]["archived"] is True
    assert storage.delegated_arena_page(("shared",)) == [], "ordinary list retains its default"


def test_arena_filter_does_not_follow_cross_arena_dependencies():
    storage, work, arenas = fixture()
    repo = WorkObjectRepository(storage)
    repo.create(Task(id="other-task", title="Other task"))
    storage.create_edge("Arena", "other", "CONTAINS_WORK", "Task", "other-task")
    storage.create_edge("Task", "task", "DEPENDS_ON", "Task", "other-task")
    work = work.model_copy(
        update={"operations": ("list", "relationships"), "relationship_types": ("dependencies",)}
    )
    arenas = CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True)
    result = project_monitor_read(
        storage, [work, arenas], "/monitor/topology/v1?arena=Arena%3Ashared"
    )
    assert {n["id"] for n in result["graph_nodes"]} == {"shared", "project", "task"}
    assert result["graph_edges"] == []
