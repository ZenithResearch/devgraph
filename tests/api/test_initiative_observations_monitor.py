from __future__ import annotations

from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ, SCOPE_WRITE


def client(scopes: frozenset[str]) -> tuple[TestClient, object]:
    services = build_services(scopes)
    return TestClient(create_app(services), raise_server_exceptions=False), services


def observation_body() -> dict:
    return {
        "id": "observation-1",
        "project_id": "external-project-1",
        "subject_kind": "github_repository",
        "subject_url": "https://github.com/Owner/Repository.git",
        "github_node_id": "R_kgDOExample",
        "source_commit": "abcdef123456",
        "title": "Portable initiative graph",
        "problem": "Public project intent is difficult to discover across hosts.",
        "desired_state": "Evidence-backed initiatives can be claimed and federated.",
        "evidence_urls": [
            "https://github.com/Owner/Repository/tree/abcdef123456/src"
        ],
        "confidence": 0.82,
        "observed_by": "scout-key-1",
    }


def auth_headers(*, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_create_get_list_and_idempotent_receipt_contract() -> None:
    api, _ = client(frozenset({SCOPE_READ, SCOPE_WRITE}))

    created = api.post(
        "/initiative-observations",
        json=observation_body(),
        headers=auth_headers(key="observation-create-1"),
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["observation"]["schema_version"] == (
        "devgraph.initiative-observation.v0"
    )
    assert payload["observation"]["subject_url"] == (
        "https://github.com/owner/repository"
    )
    assert payload["observation"]["authorship"] == "inferred"
    assert payload["observation"]["claim_status"] == "unclaimed"
    assert payload["receipt"]["subject_label"] == "Artifact"

    got = api.get(
        "/initiative-observations/observation-1",
        headers=auth_headers(),
    )
    listed = api.get(
        "/initiative-observations?descending=true&limit=10",
        headers=auth_headers(),
    )
    assert got.status_code == 200 and got.json()["id"] == "observation-1"
    assert [item["id"] for item in listed.json()["items"]] == ["observation-1"]

    duplicate = api.post(
        "/initiative-observations",
        json=observation_body(),
        headers=auth_headers(key="observation-create-1"),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["observation"] is None
    assert duplicate.json()["receipt"]["duplicate"] is True


def test_observation_contract_rejects_claim_attribution_and_bad_subjects() -> None:
    api, _ = client(frozenset({SCOPE_WRITE}))
    body = observation_body()
    body["claim_status"] = "claimed"
    assert (
        api.post(
            "/initiative-observations",
            json=body,
            headers=auth_headers(key="invalid-claim"),
        ).status_code
        == 422
    )
    body = observation_body()
    body["subject_url"] = "https://example.com/project"
    assert (
        api.post(
            "/initiative-observations",
            json=body,
            headers=auth_headers(key="invalid-subject"),
        ).status_code
        == 400
    )


def test_observation_and_monitor_reads_require_read_scope() -> None:
    api, _ = client(frozenset({SCOPE_WRITE}))
    assert api.get("/initiative-observations", headers=auth_headers()).status_code == 403
    assert api.get("/monitor/snapshot", headers=auth_headers()).status_code == 403
    assert api.get("/initiative-observations").status_code == 401


def test_monitor_snapshot_projects_work_observations_and_receipts() -> None:
    api, services = client(frozenset({SCOPE_READ, SCOPE_WRITE}))
    api.post(
        "/work/Initiative",
        json={"id": "initiative-1", "title": "Federated discovery"},
        headers=auth_headers(key="initiative-create-1"),
    )
    api.post(
        "/initiative-observations",
        json=observation_body(),
        headers=auth_headers(key="observation-create-1"),
    )
    services.storage.create_node(
        "Artifact",
        "private-artifact-1",
        {"role": "evidence", "title": "Bearer private-monitor-secret"},
    )
    services.storage.create_edge(
        "Initiative",
        "initiative-1",
        "HAS_ARTIFACT",
        "Artifact",
        "private-artifact-1",
    )

    response = api.get("/monitor/snapshot", headers=auth_headers())

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["storage"]["ready"] is True
    assert snapshot["total_work"] == 1
    assert snapshot["active_initiatives"] == 1
    assert snapshot["observation_count"] == 1
    assert snapshot["receipt_count"] == 2
    assert snapshot["pending_receipts"] == 2
    assert snapshot["observation_by_status"] == {"unclaimed": 1}
    assert {item["type"] for item in snapshot["recent_activity"]} == {
        "work",
        "observation",
        "receipt",
    }
    graph_keys = {node["key"] for node in snapshot["graph_nodes"]}
    assert "Initiative:initiative-1" in graph_keys
    assert "Artifact:observation-1" in graph_keys
    assert "Artifact:private-artifact-1" not in graph_keys
    assert len(snapshot["graph_edges"]) == 2
    assert {edge["relationship"] for edge in snapshot["graph_edges"]} == {
        "EMITTED_EVENT"
    }
    assert "private-monitor-secret" not in response.text


def test_official_frontend_is_dependency_free_and_never_embeds_a_credential() -> None:
    api, _ = client(frozenset())

    response = api.get("/")
    monitor_alias = api.get("/monitor")

    assert response.status_code == 200
    assert monitor_alias.status_code == 200
    assert monitor_alias.text == response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "Graph operations monitor" in response.text
    assert "Graph topology" in response.text
    assert "renderGraph" in response.text
    assert "Orbit: off" in response.text
    assert 'id="reader-content"' in response.text
    assert "Description / plan" in response.text
    assert "Supporting material" in response.text
    assert 'id="graph-search"' in response.text
    assert "Layout settings" in response.text
    assert "Node separation" in response.text
    assert "Reset force defaults" in response.text
    assert ".graph-node:focus-visible .graph-sphere" in response.text
    assert "terminal work items (accepted or archived)" in response.text
    assert "edgeStrengths: new Map()" in response.text
    assert "repulsionStrength: 1.5" in response.text
    assert "sessionStorage" in response.text
    assert FAKE_CREDENTIAL not in response.text


def test_local_monitor_fixture_credential_is_read_only(monkeypatch) -> None:
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "local-dev")
    monkeypatch.setenv("DEVGRAPH_MONITOR_DEMO", "1")
    from devgraph.local_app import DEFAULT_LOCAL_TOKEN, create_local_app

    api = TestClient(create_local_app(), raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {DEFAULT_LOCAL_TOKEN}"}

    snapshot = api.get("/monitor/snapshot", headers=headers)
    assert snapshot.status_code == 200
    assert {edge["relationship"] for edge in snapshot.json()["graph_edges"]} == {
        "HAS_ARTIFACT",
        "HAS_CHILD",
    }
    assert (
        api.post(
            "/initiative-observations",
            json=observation_body(),
            headers={**headers, "Idempotency-Key": "read-only-denial"},
        ).status_code
        == 403
    )
