from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ
from devgraph.model.work import Project
from devgraph.monitoring import build_monitor_snapshot


def test_selection_page_and_explicit_assets_are_public_but_snapshot_is_protected():
    client = TestClient(create_app(build_services(frozenset({SCOPE_READ}))))
    for path in ("/monitor/selection", "/monitor/selection/"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Project selection" in response.text
        assert "worker-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
    assert '/monitor/selection' in client.get('/monitor').text
    for name in (
        "core/index.mjs", "core/run.mjs", "page/app.mjs", "page/style.css",
        "page/flow-view.mjs", "page/flow-layout.mjs", "core/network.mjs", "core/draft.mjs",
    ):
        assert client.get("/monitor/selection-assets/" + name).status_code == 200
    assert client.get('/monitor/selection-assets/core/index.d.ts').status_code == 404
    assert client.get('/monitor/selection-assets/../../api/app.py').status_code == 404
    assert client.get('/monitor/snapshot').status_code == 401


def test_selection_metadata_preserves_versions_and_reports_hidden_obligations_without_ids():
    services = build_services(frozenset({SCOPE_READ}))
    work = Project(id="visible", title="Visible", version=9007199254740993)
    services.storage.create_node("Project", work.id, work.to_node_properties())
    services.storage.create_node("Artifact", "hidden-sensitive-id", {"title": "Hidden title"})
    services.storage.create_edge(
        "Project", work.id, "DEPENDS_ON", "Artifact", "hidden-sensitive-id",
    )
    services.storage.create_edge("Artifact", "hidden-sensitive-id", "BLOCKS", "Project", work.id)
    response = TestClient(create_app(services)).get(
        '/monitor/snapshot', headers={"Authorization": "Bearer " + FAKE_CREDENTIAL},
    )
    assert response.status_code == 200
    data = response.json()
    assert data['graph_nodes'][0]['version'] == '9007199254740993'
    assert data['graph_edges'] == []
    assert data['selection_scope']['edge_scan'] == 'all_stored_edges'
    assert data['selection_scope']['consistency'] == 'assembled'
    assert len(data['selection_scope']['unresolved']) == 2
    assert data['selection_scope']['read_started_at'] <= data['selection_scope']['read_finished_at']
    assert 'hidden-sensitive-id' not in response.text
    assert 'Hidden title' not in response.text


def test_existing_graph_projection_is_unchanged_and_known_dependencies_are_visible():
    services = build_services(frozenset({SCOPE_READ}))
    for work_id in ('a', 'b'):
        work = Project(id=work_id, title=work_id)
        services.storage.create_node('Project', work_id, work.to_node_properties())
    services.storage.create_edge('Project', 'a', 'DEPENDS_ON', 'Project', 'b')
    data = build_monitor_snapshot(services.storage)
    assert data['graph_edges'] == [
        {'source': 'Project:a', 'target': 'Project:b', 'relationship': 'DEPENDS_ON'},
    ]
    assert data['selection_scope']['unresolved'] == []
