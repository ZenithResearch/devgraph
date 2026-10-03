"""Authenticated board projection, bounded cards and legacy read compatibility."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task


@pytest.fixture
def fixture():
    services = build_services(frozenset({SCOPE_READ}))
    repository = WorkObjectRepository(services.storage)
    repository.create(Task(id="new-task", title="Current work"))
    repository.create(Task(id="old-task", title="Legacy work", workflow_json=None))
    return TestClient(create_app(services)), services


def read(api, path):
    return api.get(path, headers={"Authorization": "Bearer " + FAKE_CREDENTIAL})


def test_protected_reads_and_no_bearer_move(fixture):
    api, _ = fixture
    for path in ("/monitor/kanban/v1", "/workflows/v1", "/work/Task/new-task/workflow"):
        assert api.get(path).status_code == 401
        result = read(api, path)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
    response = api.post(
        "/work-operations/v1",
        headers={"Authorization": "Bearer " + FAKE_CREDENTIAL},
        json={"operation": "workflow.transition"},
    )
    assert response.status_code in (401, 403, 422, 503)


def test_legacy_absence_and_filter_counts(fixture):
    api, _ = fixture
    result = read(api, "/monitor/kanban/v1?workflow=unset").json()
    assert result["total"] == 1
    card = result["columns"][0]["items"][0]
    assert (card["stage"], card["stage_label"]) == (None, "Stage not set")
    assert read(api, "/work/Task/old-task/workflow").json()["workflow"] is None
    assert "workflow_json" not in read(api, "/work/Task/new-task").json()
    assert read(api, "/monitor/kanban/v1?stage=backlog").json()["total"] == 1
    assert read(api, "/monitor/kanban/v1?column=backlog").json()["total"] == 2


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "kind=Artifact",
        "column=invalid",
        "workflow=no",
        "stage=accepted",
        "archived=true",
        "scope=Proposal/new-task",
        "after=Task/new-task",
        "q=a&q=b",
        "secret=abc",
        "descendants=1",
    ],
)
def test_bad_filters_are_bounded(fixture, query):
    api, _ = fixture
    assert read(api, "/monitor/kanban/v1?" + query).status_code == 400


@pytest.mark.parametrize("size", [250, 1500])
def test_large_board_has_complete_counts_bounded_render_payload(fixture, size):
    api, services = fixture
    repo = WorkObjectRepository(services.storage)
    for i in range(size - 2):
        repo.create(Task(id=f"fixture-{i:04}", title=f"Work {i}"))
    start = time.monotonic()
    result = read(api, "/monitor/kanban/v1").json()
    assert result["total"] == size
    assert len(result["columns"][0]["items"]) == 30
    assert len(json.dumps(result)) < 100_000
    assert time.monotonic() - start < 3
    page = read(
        api,
        "/monitor/kanban/v1?column=backlog&after="
        + result["columns"][0]["next_cursor"]
        + "&revision="
        + result["revision"],
    ).json()
    assert page["total"] == size
    assert set(c["key"] for c in page["columns"][0]["items"]).isdisjoint(
        c["key"] for c in result["columns"][0]["items"]
    )


def test_page_security_and_asset_allowlist(fixture):
    api, _ = fixture
    for path in ("/monitor/kanban/", "/monitor/kanban-assets/app.mjs"):
        response = api.get(path)
        assert response.status_code == 200
        assert "content-security-policy" in response.headers
    assert api.get("/monitor/kanban-assets/secrets.json").status_code == 404


def test_scope_picker_is_bounded_and_searchable(fixture):
    from devgraph.model.work import Project
    api, services = fixture
    repo = WorkObjectRepository(services.storage)
    for i in range(250):
        repo.create(Project(id=f"project-{i:03}", title=f"Project {i}"))
    board = read(api, "/monitor/kanban/v1?scope=Project/project-249").json()
    assert board["scope_count"] == 250 and board["scopes_truncated"]
    assert len(board["scopes"]) == 200
    assert "Project/project-249" in {s["key"] for s in board["scopes"]}
    result = read(api, "/monitor/kanban/v1?q=project-249").json()
    assert len(result["scopes"]) == 1


def test_frontend_workflow_privacy_and_uncertain_dispatch():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for frontend contract checks")
    subprocess.run([node, "--test", str(Path(__file__).parents[1] / "frontend_kanban.mjs")],
                   check=True, capture_output=True, text=True, timeout=30)
