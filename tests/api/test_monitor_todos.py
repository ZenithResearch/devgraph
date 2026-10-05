"""Exact Todo reads never substitute a subtype or extend mutable Work admission."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ, SCOPE_WRITE
from devgraph.model.base import WorkStatus
from devgraph.model.work import Initiative, Issue, Project, Proposal, Task, Todo
from devgraph.storage.base import NodeRecord, StorageUnavailable

HEADERS = {"Authorization": "Bearer " + FAKE_CREDENTIAL}
PATH = "/monitor/todos/v1"


def add_todo(storage, item_id, *, status=WorkStatus.DRAFT, title=None, **kwargs):
    todo = Todo(id=item_id, title=title or item_id, workflow_json=None, **kwargs)
    if status != WorkStatus.ARCHIVED:
        todo = replace(todo, status=status)
    storage.create_node("Todo", todo.id, todo.to_node_properties())
    if status == WorkStatus.ARCHIVED:
        storage.archive_node("Todo", todo.id, todo.with_status(status).to_node_properties())
    return todo


def fixture():
    services = build_services(frozenset({SCOPE_READ}))
    for item_id, status in [
        ("a", WorkStatus.DRAFT), ("b", WorkStatus.REVIEW),
        ("c", WorkStatus.ACCEPTED), ("d", WorkStatus.ARCHIVED), ("e", WorkStatus.REVIEW),
    ]:
        add_todo(services.storage, item_id, status=status, title="Daily " + item_id)
    for cls in (Proposal, Initiative, Project, Issue, Task):
        work = cls(id=cls.__name__.lower(), title="Daily subtype")
        services.storage.create_node(work.kind, work.id, work.to_node_properties())
    # Simulate malformed legacy storage without admitting this through writes.
    services.storage._nodes[("Todo", "mismatch")] = NodeRecord(
        "Todo", "mismatch", {"kind": "Task", "title": "Daily mismatch"})
    services.storage.create_node("Todo", "legacy", {"title": "Daily legacy"})
    return services, TestClient(create_app(services))


def test_exact_base_pagination_full_counts_and_status_facet_scope():
    services, api = fixture()
    first = api.get(PATH + "?limit=2", headers=HEADERS)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    page = first.json()
    assert page["schema"] == "devgraph.todos.v1"
    assert [x["id"] for x in page["items"]] == ["a", "b"]
    assert all(x["kind"] == "Todo" and "description" not in x for x in page["items"])
    assert page["counts"] == dict(total=4, draft=1, review=2, accepted=1, archived=0)
    assert page["matching_count"] == 4
    assert page["next_after_id"] == "b" and page["has_more"]
    second = api.get(PATH + "?limit=2&after_id=b", headers=HEADERS).json()
    assert [x["id"] for x in second["items"]] == ["c", "e"]
    assert second["counts"] == page["counts"]
    assert second["next_after_id"] is None and second["has_more"] is False
    review = api.get(PATH + "?status=review&limit=1&after_id=b", headers=HEADERS).json()
    assert [x["id"] for x in review["items"]] == ["e"]
    assert review["matching_count"] == 2 and review["counts"] == page["counts"]
    assert services.authorized_graph._audit_log.records[-1].operation == "monitor_todos"


def test_archive_search_and_no_results_are_distinct_from_subtypes():
    _, api = fixture()
    archive = api.get(PATH + "?archived=only", headers=HEADERS).json()
    assert [x["id"] for x in archive["items"]] == ["d"]
    assert archive["counts"] == dict(total=1, draft=0, review=0, accepted=0, archived=1)
    included = api.get(PATH + "?archived=include&q=DAILY", headers=HEADERS).json()
    assert included["counts"]["total"] == 5
    none = api.get(PATH + "?q=subtype", headers=HEADERS).json()
    assert none["items"] == [] and none["counts"]["total"] == 0
    assert none["matching_count"] == 0 and none["next_after_id"] is None


def test_detail_redaction_identity_and_lossless_signed64_numbers():
    services, api = fixture()
    add_todo(services.storage, "safe", title="Review password=private-value",
             description="Read Bearer sensitive-material", priority=-9223372036854775808,
             version=9223372036854775807)
    result = api.get(PATH + "/safe", headers=HEADERS)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["title"] == "Review [REDACTED]"
    assert data["description"] == "Read [REDACTED]"
    assert data["priority"] == "-9223372036854775808"
    assert data["version"] == "9223372036854775807"
    assert data["created_at"] and data["updated_at"]
    assert services.authorized_graph._audit_log.records[-1].operation == "monitor_todo"
    for q in ("private-value", "sensitive-material"):
        search = api.get(PATH, params={"q": q}, headers=HEADERS).json()
        assert search["matching_count"] == 0 and search["counts"]["total"] == 0
    visible = api.get(PATH, params={"q": "[REDACTED]"}, headers=HEADERS).json()
    assert [x["id"] for x in visible["items"]] == ["safe"]
    for item_id in ("task", "mismatch", "legacy", "missing"):
        assert api.get(PATH + "/" + item_id, headers=HEADERS).status_code == 404


@pytest.mark.parametrize("query", [
    "limit=0", "limit=101", "limit=true", "limit=1&limit=2", "kind=Task",
    "status=done", "status=", "archived=true", "after_id=../bad", "after_id=",
    "q=" + "a" * 201, "q=%00", "q=%0A", "q=%GG", "q=%FF", "q=a&q=b", "q",
    "order=invalid", "queue=all", "order=priority&after_id=a",
    "after_priority=2", "order=priority&after_priority=2",
    "order=priority&after_id=a&after_priority=9223372036854775808",
])
def test_filters_are_strict_and_safe(query):
    _, api = fixture()
    response = api.get(PATH + "?" + query, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_todo_filter"


def test_not_started_priority_queue_preserves_exact_base_type_and_legacy_defaults():
    services, api = fixture()
    for item_id, priority in [("z-high", 9223372036854775807), ("low", -9223372036854775808),
                              ("b-tie", 2), ("a-tie", 2)]:
        add_todo(services.storage, item_id, priority=priority)
    query = {"queue": "not_started", "order": "priority", "limit": 2}
    first = api.get(PATH, params=query, headers=HEADERS).json()
    assert first["matching_count"] == 5
    assert first["counts"] == dict(total=5, draft=5, review=0, accepted=0, archived=0)
    assert [x["id"] for x in first["items"]] == ["z-high", "a-tie"]
    assert first["next_after_priority"] == "2"
    second = api.get(PATH, params={**query, "after_id": first["next_after_id"],
                                  "after_priority": first["next_after_priority"]},
                     headers=HEADERS).json()
    assert [x["id"] for x in second["items"]] == ["b-tie", "a"]
    assert second["matching_count"] == 5
    assert "next_after_priority" not in api.get(PATH, headers=HEADERS).json()
    empty = api.get(PATH, params={**query, "q": "subtype"}, headers=HEADERS).json()
    assert empty["matching_count"] == 0


def test_auth_precedes_filter_parsing_and_storage_and_writes_remain_unavailable():
    for scopes, expected in [(frozenset(), 403), (frozenset({SCOPE_WRITE}), 403)]:
        services = build_services(scopes)
        def forbidden_storage(*args):
            raise AssertionError("storage must not be called")
        services.storage.todo_page = forbidden_storage
        services.storage.todo_detail = forbidden_storage
        api = TestClient(create_app(services))
        assert api.get(PATH + "?limit=bad", headers=HEADERS).status_code == expected
        assert api.get(PATH + "/anything", headers=HEADERS).status_code == expected
        assert api.get(PATH).status_code == 401
        assert api.get(PATH + "/anything").status_code == 401
    _, api = fixture()
    assert api.post(PATH, json={"title": "No write"}, headers=HEADERS).status_code == 405
    assert api.get("/work/Todo/a", headers=HEADERS).status_code == 422
    assert api.patch("/work/Todo/a", json={}, headers=HEADERS).status_code == 422


def test_storage_failure_is_not_an_empty_success_and_has_no_sensitive_detail():
    services, api = fixture()
    def fail(*args):
        raise StorageUnavailable("secret=must-not-appear")
    services.storage.todo_page = fail
    response = api.get(PATH, headers=HEADERS)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "must-not-appear" not in response.text


def test_unfiltered_default_and_genuinely_empty_base_collection():
    _, api = fixture()
    response = api.get(PATH, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["matching_count"] == 4
    services = build_services(frozenset({SCOPE_READ}))
    work = Task(id="subtype", title="Task remains separate")
    services.storage.create_node("Task", work.id, work.to_node_properties())
    result = TestClient(create_app(services)).get(PATH, headers=HEADERS)
    assert result.status_code == 200
    assert result.json()["items"] == []
    assert result.json()["counts"] == dict(total=0, draft=0, review=0, accepted=0, archived=0)
