from __future__ import annotations

from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project, Task
from devgraph.storage.delegated import DelegatedWorkSelector
from devgraph.storage.memory import MemoryGraphStorage

KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")


def create(storage: MemoryGraphStorage, *items) -> None:
    repository = WorkObjectRepository(storage)
    for item in items:
        repository.create(item)


def test_arena_descendant_filter_runs_before_work_pagination() -> None:
    storage = MemoryGraphStorage()
    root = Initiative(id="z-root", title="Root")
    project = Project(id="z-project", title="Project")
    inside = Issue(id="z-inside", title="Inside")
    outside = Issue(id="a-outside", title="Outside")
    create(storage, root, project, inside, outside)
    storage.create_node("Arena", "arena-a", {"kind": "Arena"})
    storage.create_edge("Arena", "arena-a", "CONTAINS_WORK", root.kind, root.id)
    storage.create_edge(root.kind, root.id, "HAS_CHILD", project.kind, project.id)
    storage.create_edge(project.kind, project.id, "HAS_CHILD", inside.kind, inside.id)

    page = storage.delegated_work_page(
        "Issue",
        selectors=(DelegatedWorkSelector(arena_ids=("arena-a",)),),
        work_kinds=KINDS,
        include_archived=False,
        limit=1,
    )

    assert [node.id for node in page] == ["z-inside"]


def test_exact_selector_and_kind_filter_run_before_relationship_pagination() -> None:
    storage = MemoryGraphStorage()
    blocked = Task(id="blocked", title="Blocked")
    hidden = Task(id="a-hidden", title="Hidden")
    visible = Task(id="z-visible", title="Visible")
    create(storage, blocked, hidden, visible)
    storage.create_edge(hidden.kind, hidden.id, "BLOCKS", blocked.kind, blocked.id)
    storage.create_edge(visible.kind, visible.id, "BLOCKS", blocked.kind, blocked.id)

    page = storage.delegated_related_work_nodes(
        blocked.kind,
        blocked.id,
        "BLOCKS",
        incoming=True,
        selectors=(DelegatedWorkSelector(work_ids=("Task/z-visible",)),),
        work_kinds=("Task",),
        include_archived=False,
        limit=1,
    )

    assert [node.id for node in page] == ["z-visible"]


def test_arena_members_are_intersected_with_work_grant_before_pagination() -> None:
    storage = MemoryGraphStorage()
    hidden = Task(id="a-hidden", title="Hidden")
    visible = Task(id="z-visible", title="Visible")
    create(storage, hidden, visible)
    storage.create_node("Arena", "arena-a", {"kind": "Arena"})
    for item in (hidden, visible):
        storage.create_edge("Arena", "arena-a", "CONTAINS_WORK", item.kind, item.id)

    page = storage.delegated_arena_member_page(
        "arena-a",
        selectors=(DelegatedWorkSelector(work_ids=("Task/z-visible",)),),
        work_kinds=("Task",),
        include_archived=False,
        limit=1,
    )

    assert [item.node.id for item in page] == ["z-visible"]


def test_selectors_are_conjunctive_within_a_group_and_disjunctive_across_groups() -> None:
    storage = MemoryGraphStorage()
    in_arena = Task(id="in-arena", title="In Arena")
    exact_only = Task(id="exact-only", title="Exact only")
    create(storage, in_arena, exact_only)
    storage.create_node("Arena", "arena-a", {"kind": "Arena"})
    storage.create_edge("Arena", "arena-a", "CONTAINS_WORK", in_arena.kind, in_arena.id)

    conjunctive = storage.delegated_work_page(
        "Task",
        selectors=(DelegatedWorkSelector(work_ids=("Task/exact-only",), arena_ids=("arena-a",)),),
        work_kinds=("Task",),
        include_archived=False,
    )
    disjunctive = storage.delegated_work_page(
        "Task",
        selectors=(
            DelegatedWorkSelector(work_ids=("Task/exact-only",)),
            DelegatedWorkSelector(arena_ids=("arena-a",)),
        ),
        work_kinds=("Task",),
        include_archived=False,
    )

    assert conjunctive == []
    assert [node.id for node in disjunctive] == ["exact-only", "in-arena"]
