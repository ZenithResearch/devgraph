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


def test_scoped_edges_require_both_endpoints_before_limit():
    storage = MemoryGraphStorage()
    create(
        storage,
        Task(id="source", title="Source"),
        Task(id="a-hidden", title="Hidden"),
        Task(id="z-visible", title="Visible"),
    )
    storage.create_edge("Task", "source", "BLOCKS", "Task", "a-hidden")
    storage.create_edge("Task", "source", "BLOCKS", "Task", "z-visible")
    edges = storage.delegated_work_edges(
        (("Task", "source"), ("Task", "z-visible")), ("BLOCKS",), limit=1
    )
    assert [(edge.from_id, edge.to_id) for edge in edges] == [("source", "z-visible")]
    assert not storage.delegated_work_edges((("Task", "source"),), ("BLOCKS",), limit=1)


def test_neo4j_scoped_edge_query_filters_targets_before_pagination():
    from tests.storage.test_neo4j_persistence_contract import _storage_with_rows

    storage, calls = _storage_with_rows([])
    assert (
        storage.delegated_work_edges((("Task", "source"), ("Task", "target")), ("BLOCKS",), limit=1)
        == []
    )
    query, parameters = calls[0]
    assert query.index("any(k IN $keys") < query.index("LIMIT $limit")
    assert parameters["keys"] == [
        {"label": "Task", "id": "source"},
        {"label": "Task", "id": "target"},
    ]


def test_arena_edges_require_both_explicit_endpoints_before_limit():
    import pytest

    storage = MemoryGraphStorage()
    for arena in ("a-hidden", "z-visible"):
        storage.create_node("Arena", arena, {"kind": "Arena"})
    for task in ("a-hidden", "z-visible"):
        create(storage, Task(id=task, title=task))
        for arena in ("a-hidden", "z-visible"):
            storage.create_edge("Arena", arena, "CONTAINS_WORK", "Task", task)
    rows = storage.delegated_arena_edges(("z-visible",), (("Task", "z-visible"),), limit=1)
    assert [(r.from_id, r.to_id) for r in rows] == [("z-visible", "z-visible")]
    with pytest.raises(ValueError):
        storage.delegated_work_edges((("Arena", "z-visible"),), ("CONTAINS_WORK",), limit=1)
    with pytest.raises(ValueError):
        storage.delegated_arena_edges(("z-visible", "z-visible"), (), limit=1)


def test_neo4j_arena_edge_filters_both_endpoints_before_limit():
    from tests.storage.test_neo4j_persistence_contract import _storage_with_rows

    storage, calls = _storage_with_rows([])
    assert storage.delegated_arena_edges(("visible",), (("Task", "allowed"),), limit=1) == []
    query, params = calls[0]
    assert query.index("key.label IN labels(target)") < query.index("LIMIT $limit")
    assert query.index("source.id IN $arena_ids") < query.index("LIMIT $limit")
    assert params["arena_ids"] == ["visible"]
    assert params["keys"] == [dict(label="Task", id="allowed")]


def test_arena_edge_bound_supports_union_of_distinct_valid_grants():
    import pytest

    from devgraph.storage.delegated import validate_arena_edge_selection
    ids = tuple(f"arena-{i}" for i in range(4096))
    validate_arena_edge_selection(ids, (), 1)
    with pytest.raises(ValueError):
        validate_arena_edge_selection((*ids, "overflow"), (), 1)
