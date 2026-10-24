"""A published UI shell never pretends to activate endpoint authority."""

from fastapi.testclient import TestClient

from devgraph.wallet_monitor_shell_runtime import create_app


def test_setup_shell_serves_wallet_under_connect_graph_and_keeps_access_closed(
    tmp_path, monkeypatch
):
    from scripts.build_wallet_monitor_shell import build_shell

    shell = tmp_path / "site"
    build_shell(shell)
    monkeypatch.setenv("DEVGRAPH_WALLET_MONITOR_SHELL", str(shell))
    monkeypatch.setenv("DEVGRAPH_WALLET_MONITOR_ORIGIN", "https://graph.example:9443")
    http = TestClient(create_app(), base_url="http://graph.example:9443")
    for path in ("/", "/monitor", "/monitor/", "/monitor/kanban", "/monitor/kanban/"):
        response = http.get(path, follow_redirects=False)
        assert response.status_code == 200
        assert 'id="wallet-read-connection"' in response.text
        assert 'type="password"' not in response.text
        assert 'id="token"' not in response.text
        assert response.headers["cache-control"] == "no-store"
    assert "Connect to your graph with Wallet" in http.get("/").text
    for path in (
        "/monitor/topology-assets/wallet-access.js",
        "/assets/credential-v2/read-access.mjs",
        "/assets/kanban/app.mjs",
    ):
        assert http.get(path).status_code == 200
    assert http.get("/monitor/wallet-access/v1").status_code == 503
    assert http.get("/live").json()["credential_requests_enabled"] is False
    for path in (
        "/monitor/wallet-access/v1/enrollments",
        "/devgraph/access-requests/v2/actions",
        "/work-operations/v2",
    ):
        assert http.post(path, json={}).status_code == 404
    assert http.get("/", headers={"Host": "other.example"}).status_code == 421
    assert http.get("/private-config.json").status_code == 404
