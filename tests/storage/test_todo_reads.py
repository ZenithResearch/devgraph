"""Bounded Todo-label queries and exact-kind storage parity without live Neo4j."""

from copy import deepcopy

import pytest
from tests.storage.test_neo4j_persistence_contract import _storage_with_row_batches

from devgraph.model.work import Todo
from devgraph.storage.base import NodeRecord, StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.todos import SEARCH_SOURCE_LIMIT, TodoQuery


def row(item_id="a", title="Review", **kwargs):
    todo = Todo(id=item_id, title=title, workflow_json=None, **kwargs)
    return {"labels": ["Todo"], "id": item_id, "archived": False,
            "properties": {"id": item_id, "archived": False, **todo.to_node_properties()}}


def aggregate(rows):
    return [{"total": len(rows), "draft": len(rows), "review": 0, "accepted": 0,
             "archived": 0, "matching_count": len(rows), "remaining_count": len(rows),
             "nodes": rows}]


def test_memory_and_neo4j_agree_on_bounded_exact_label_counts_page_and_detail():
    rows = [row("a"), row("b")]
    neo, calls = _storage_with_row_batches(aggregate(rows), [rows[0]])
    memory = MemoryGraphStorage()
    for r in rows:
        props = {k: v for k, v in r["properties"].items() if k not in ("id", "archived")}
        memory.create_node("Todo", r["id"], props)
    query = TodoQuery(limit=1)
    assert neo.todo_page(query) == memory.todo_page(query)
    assert neo.todo_detail("a") == memory.todo_detail("a")
    statement, params = calls[0]
    assert statement.text.count("MATCH (n:Todo)") == 2
    assert statement.text.count("labels(n) = ['Todo'] AND n.kind = 'Todo'") == 2
    assert "LIMIT $limit" in statement.text and "MATCH (n)" not in statement.text
    assert statement.timeout == 5.0
    assert params == dict(archived="exclude", status=None, after_id=None, limit=2)
    assert "LIMIT 2" in calls[1][0].text


@pytest.mark.parametrize("mutate", [
    lambda rows: rows[0].update(labels=["Todo", "Task"]),
    lambda rows: rows[0]["properties"].update(kind="Task"),
    lambda rows: rows.append(deepcopy(rows[0])),
])
def test_driver_rows_cannot_smuggle_subtypes_or_duplicate_identities(mutate):
    rows = [row()]
    mutate(rows)
    storage, _ = _storage_with_row_batches(aggregate(rows))
    with pytest.raises(StorageUnavailable):
        storage.todo_page(TodoQuery(limit=2))


def test_search_counts_use_redacted_titles_and_batch_only_selected_descriptions():
    rows = [row("a", "Review token=hidden-phrase"), row("b", "Review normal")]
    source = deepcopy(rows)
    for entry in source:
        entry["properties"] = {k: v for k, v in entry["properties"].items()
                               if k in ("id", "archived", "kind", "title", "status", "version")}
    storage, calls = _storage_with_row_batches(source)
    result = storage.todo_page(TodoQuery(q="hidden-phrase"))
    assert not result.nodes and result.counts["total"] == 0
    assert len(calls) == 1 and calls[0][1]["source_limit"] == SEARCH_SOURCE_LIMIT + 1
    assert "description" not in calls[0][0].text
    storage, calls = _storage_with_row_batches(source, [rows[0]])
    result = storage.todo_page(TodoQuery(q="[REDACTED]", limit=1))
    assert [node.id for node in result.nodes] == ["a"] and result.matching_count == 1
    assert calls[1][1] == {"ids": ["a"], "limit": 3}
    assert "n.id IN $ids" in calls[1][0].text


def test_search_budget_and_change_between_metadata_and_hydration_fail_closed():
    storage, _ = _storage_with_row_batches([{}] * (SEARCH_SOURCE_LIMIT + 1))
    with pytest.raises(StorageUnavailable, match="capacity"):
        storage.todo_page(TodoQuery(q="find"))
    original = row()
    changed = row(title="Different")
    storage, _ = _storage_with_row_batches([original], [changed])
    with pytest.raises(StorageUnavailable, match="changed"):
        storage.todo_page(TodoQuery(q="Review"))


def test_memory_ignores_wrong_label_wrong_kind_and_untyped_legacy_rows():
    storage = MemoryGraphStorage()
    storage._nodes = {
        (label, identity): NodeRecord(label, identity, props)
        for label, identity, props in [
            ("Task", "subtype", {"kind": "Todo", "title": "Wrong label"}),
            ("Todo", "mismatch", {"kind": "Task", "title": "Wrong kind"}),
            ("Todo", "legacy", {"title": "No kind"}),
        ]
    }
    assert storage.todo_page(TodoQuery()).counts["total"] == 0
    assert storage.todo_detail("mismatch") is None
    assert storage.todo_detail("legacy") is None


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"after_id": "bad/id"},
    {"status": "done"}, {"archived": "yes"}, {"q": "x" * 201}, {"q": "\x00"},
])
def test_query_limits_validated_before_storage(options):
    with pytest.raises(ValueError):
        TodoQuery(**options)


@pytest.mark.parametrize("counts,remaining,nodes,query", [
    (dict(total=2, draft=2, review=0, accepted=0, archived=0), 2, [], TodoQuery(limit=1)),
    (dict(total=3, draft=3, review=0, accepted=0, archived=0), 2, [row("b")],
     TodoQuery(limit=2, after_id="a")),
    (dict(total=1, draft=0, review=1, accepted=0, archived=0), 1, [row()], TodoQuery()),
    (dict(total=0, draft=0, review=0, accepted=0, archived=0), 0, [row()], TodoQuery()),
])
def test_aggregate_and_page_disagreement_fails_instead_of_inventing_empty_or_complete(
    counts, remaining, nodes, query
):
    storage, _ = _storage_with_row_batches([{
        **counts, "matching_count": counts["total"], "remaining_count": remaining, "nodes": nodes,
    }])
    with pytest.raises(StorageUnavailable):
        storage.todo_page(query)


def test_empty_page_after_end_and_full_page_use_cursor_relative_count():
    counts = dict(total=5, draft=5, review=0, accepted=0, archived=0)
    storage, calls = _storage_with_row_batches([
        {**counts, "matching_count": 5, "remaining_count": 0, "nodes": []}
    ])
    result = storage.todo_page(TodoQuery(after_id="z", limit=2))
    assert result.nodes == () and result.matching_count == 5 and result.remaining_count == 0
    assert "AS remaining_count" in calls[0][0].text
    storage, _ = _storage_with_row_batches([
        {**counts, "matching_count": 5, "remaining_count": 3, "nodes": [row("c"), row("d")]}
    ])
    result = storage.todo_page(TodoQuery(after_id="b", limit=1))
    assert len(result.nodes) == 2 and result.remaining_count == 3


def test_priority_queue_metadata_and_hydration_agree_across_adapters():
    rows = [row("a", priority=-9223372036854775808), row("z", priority=9223372036854775807)]
    storage, calls = _storage_with_row_batches(rows, rows)
    memory = MemoryGraphStorage()
    for r in rows:
        memory.create_node("Todo", r["id"], {
            k: v for k, v in r["properties"].items() if k not in ("id", "archived")})
    query = TodoQuery(queue="not_started", order="priority", limit=1)
    result = storage.todo_page(query)
    assert result == memory.todo_page(query)
    assert [n.id for n in result.nodes] == ["z", "a"]
    assert ".priority, .workflow_json" in calls[0][0].text
    assert calls[0][1]["source_limit"] == SEARCH_SOURCE_LIMIT + 1
    assert calls[1][1]["ids"] == ["z", "a"]
    changed = deepcopy(rows)
    changed[1]["properties"]["priority"] = 0
    storage, _ = _storage_with_row_batches(rows, changed)
    with pytest.raises(StorageUnavailable, match="changed"):
        storage.todo_page(query)


def test_queue_obeys_recorded_workflow_instead_of_assuming_every_draft_is_unstarted():
    from devgraph.storage.todos import select_todo_page
    from devgraph.workflow_contract import WorkflowState, encode_state

    storage = MemoryGraphStorage()
    records = []
    for key, state, status in [
        ("old-draft", None, "draft"), ("old-review", None, "review"),
        ("old-accepted", None, "accepted"), ("backlog", "backlog", "accepted"),
        ("started", "intake", "draft"),
    ]:
        props = row(key)["properties"]
        props.update(status=status, workflow_json=encode_state(
            WorkflowState(workflow_id="execution.v1", stage=state)) if state else None)
        records.append(NodeRecord("Todo", key, props))
    result = select_todo_page(records, TodoQuery(queue="not_started", order="priority"))
    assert [n.id for n in result.nodes] == ["backlog", "old-draft"]
    assert result.counts["total"] == 2
    storage._nodes = {("Todo", "invalid"): NodeRecord("Todo", "invalid", {
        **row()["properties"], "workflow_json": "invalid"})}
    with pytest.raises(StorageUnavailable, match="workflow"):
        storage.todo_page(TodoQuery(queue="not_started"))
