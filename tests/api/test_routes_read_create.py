"""Commit 3: read routes over the authorized façade.

Reads pass the opaque credential to the façade; denied calls return
safe 401/403 problem details with no audit-record side effects. All
fixtures are synthetic.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ, SCOPE_WRITE
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue, Project


def _client(scopes: frozenset[str]) -> tuple[TestClient, object]:
    services = build_services(scopes)
    services.authorized_graph._repository = WorkObjectRepository(services.storage)
    app = create_app(services)
    return TestClient(app, raise_server_exceptions=False), services


def _seed_parent_child(services) -> None:
    storage = services.storage
    project = Project(id="project-1", title="Parent project")
    issue = Issue(id="issue-1", title="Child issue", priority=3)
    storage.create_node("Project", project.id, project.to_node_properties())
    storage.create_node("Issue", issue.id, issue.to_node_properties())
    storage.create_edge("Project", "project-1", "HAS_CHILD", "Issue", "issue-1")


class TestReads:
    def test_children_returns_envelopes(self) -> None:
        client, services = _client(frozenset({SCOPE_READ}))
        _seed_parent_child(services)
        response = client.get(
            "/work/Project/project-1/children",
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 200
        (child,) = response.json()
        assert child["id"] == "issue-1"
        assert child["kind"] == "Issue"
        assert child["priority"] == 3

    def test_blockers_route_serves_empty_list(self) -> None:
        client, services = _client(frozenset({SCOPE_READ}))
        services.storage.create_node("Task", "task-1", {"title": "t"})
        response = client.get(
            "/tasks/task-1/blockers",
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_unknown_kind_is_rejected_by_validation(self) -> None:
        client, _ = _client(frozenset({SCOPE_READ}))
        response = client.get(
            "/work/WorkRequest/x/children",
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 422


class TestDenials:
    def test_missing_credential_returns_401_without_audit(self) -> None:
        client, services = _client(frozenset({SCOPE_READ}))
        _seed_parent_child(services)
        response = client.get("/work/Project/project-1/children")
        assert response.status_code == 401
        assert services.authorized_graph._audit_log.records == []

    def test_missing_scope_returns_403_without_audit_or_echo(self) -> None:
        client, services = _client(frozenset())
        _seed_parent_child(services)
        response = client.get(
            "/work/Project/project-1/children",
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 403
        assert FAKE_CREDENTIAL not in json.dumps(response.json())
        assert services.authorized_graph._audit_log.records == []


def test_generic_get_list_and_create_contract() -> None:
    client, services = _client(frozenset({SCOPE_READ, SCOPE_WRITE}))
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}", "Idempotency-Key": "create-1"}
    created = client.post("/work/Issue", json={"id": "i-2", "title": "Created"}, headers=headers)
    assert created.status_code == 201
    assert created.json()["work"]["status"] == "draft"
    got = client.get("/work/Issue/i-2", headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"})
    assert got.status_code == 200 and got.json()["id"] == "i-2"
    listed = client.get("/work/Issue", headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"})
    assert [item["id"] for item in listed.json()["items"]] == ["i-2"]
    duplicate = client.post("/work/Issue", json={"id": "i-2", "title": "Created"}, headers=headers)
    assert duplicate.status_code == 200 and duplicate.json()["work"] is None


def test_create_requires_key_and_rejects_unknown_kind() -> None:
    client, _ = _client(frozenset({SCOPE_WRITE}))
    auth = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    assert (
        client.post("/work/Issue", json={"id": "i", "title": "x"}, headers=auth).status_code == 422
    )
    assert (
        client.post(
            "/work/Decision",
            json={"id": "d", "title": "x"},
            headers={**auth, "Idempotency-Key": "k"},
        ).status_code
        == 422
    )
