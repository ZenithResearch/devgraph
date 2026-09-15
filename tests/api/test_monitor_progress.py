from __future__ import annotations

from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ, SCOPE_WRITE


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _create(api: TestClient, kind: str, work_id: str) -> None:
    response = api.post(
        f"/work/{kind}",
        json={"id": work_id, "title": work_id.replace("-", " ").title()},
        headers=_headers(key=f"create-{work_id}"),
    )
    assert response.status_code == 201


def _nodes_by_key(api: TestClient) -> dict[str, dict]:
    response = api.get("/monitor/snapshot", headers=_headers())
    assert response.status_code == 200
    return {node["key"]: node for node in response.json()["graph_nodes"]}


def test_project_and_issue_progress_derive_from_canonical_child_graph() -> None:
    services = build_services(frozenset({SCOPE_READ, SCOPE_WRITE}))
    api = TestClient(create_app(services), raise_server_exceptions=False)
    for kind, work_id in (
        ("Project", "monitor-progress"),
        ("Issue", "monitor-progress-bar"),
        ("Issue", "progress-undivided"),
        ("Task", "progress-done"),
        ("Task", "progress-open"),
        ("Task", "progress-archived"),
    ):
        _create(api, kind, work_id)

    accepted = api.post(
        "/work/Task/progress-done/status",
        json={"status": "accepted"},
        headers=_headers(key="accept-progress-done"),
    )
    archived = api.post(
        "/work/Task/progress-archived/archive",
        headers=_headers(key="archive-progress-task"),
    )
    assert accepted.status_code == 200
    assert archived.status_code == 200

    services.storage.create_edge(
        "Project", "monitor-progress", "HAS_CHILD", "Issue", "monitor-progress-bar"
    )
    services.storage.create_edge(
        "Project", "monitor-progress", "HAS_CHILD", "Issue", "progress-undivided"
    )
    for task_id in ("progress-done", "progress-open", "progress-archived"):
        services.storage.create_edge(
            "Issue", "monitor-progress-bar", "HAS_CHILD", "Task", task_id
        )

    nodes = _nodes_by_key(api)
    expected_counts = {"draft": 1, "review": 0, "accepted": 1, "archived": 1}
    assert nodes["Issue:monitor-progress-bar"]["progress"] == {
        "schema_version": "devgraph.work-progress.v0",
        "basis": "child_tasks",
        "completed": 2,
        "total": 3,
        "percent": 67,
        "status_counts": expected_counts,
    }
    assert nodes["Project:monitor-progress"]["progress"] == {
        "schema_version": "devgraph.work-progress.v0",
        "basis": "leaf_work",
        "completed": 2,
        "total": 4,
        "percent": 50,
        "status_counts": {
            "draft": 2,
            "review": 0,
            "accepted": 1,
            "archived": 1,
        },
    }
    assert "progress" not in nodes["Task:progress-done"]


def test_progress_falls_back_to_leaf_issues_then_selected_work_status() -> None:
    services = build_services(frozenset({SCOPE_READ, SCOPE_WRITE}))
    api = TestClient(create_app(services), raise_server_exceptions=False)
    for kind, work_id in (
        ("Project", "project-with-issues"),
        ("Issue", "accepted-issue"),
        ("Issue", "standalone-issue"),
    ):
        _create(api, kind, work_id)

    accepted = api.post(
        "/work/Issue/accepted-issue/status",
        json={"status": "accepted"},
        headers=_headers(key="accept-issue"),
    )
    assert accepted.status_code == 200
    services.storage.create_edge(
        "Project", "project-with-issues", "HAS_CHILD", "Issue", "accepted-issue"
    )

    nodes = _nodes_by_key(api)
    assert nodes["Project:project-with-issues"]["progress"]["basis"] == "leaf_work"
    assert nodes["Project:project-with-issues"]["progress"]["percent"] == 100
    assert nodes["Issue:accepted-issue"]["progress"]["basis"] == "self_status"
    assert nodes["Issue:accepted-issue"]["progress"]["percent"] == 100
    assert nodes["Issue:standalone-issue"]["progress"]["basis"] == "self_status"
    assert nodes["Issue:standalone-issue"]["progress"]["percent"] == 0
