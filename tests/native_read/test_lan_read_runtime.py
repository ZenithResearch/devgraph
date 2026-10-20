import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.lan_read_runtime import LanReadBoundary, lan_origin


@pytest.mark.parametrize(
    "origin", ["https://192.168.50.10:18891", "https://10.1.2.3", "https://172.16.0.1"]
)
def test_lan_origin_requires_explicit_private_address(origin):
    assert lan_origin(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test",
        "https://8.8.8.8",
        "https://127.0.0.1",
        "https://169.254.1.2",
        "https://[::1]",
        "http://192.168.50.10",
        "https://192.168.50.10/",
    ],
)
def test_other_origins_cannot_enable_lan_mode(origin):
    with pytest.raises(ValueError):
        lan_origin(origin)


def test_listener_survives_test_timer_but_keeps_closed_routes_and_downstream_denial():
    app = FastAPI()

    @app.get("/.well-known/devgraph-connection.json")
    def metadata():
        return {"discovery": True}

    @app.post("/devgraph/native-read/v2/requests", status_code=401)
    def protected():
        return {"error": "credential_required"}

    boundary = LanReadBoundary(app, origin="https://192.168.50.10:18891")
    with TestClient(boundary, base_url="https://192.168.50.10:18891") as client:
        assert boundary.expires_at == 0
        assert client.get("/.well-known/devgraph-connection.json").status_code == 200
        assert client.post("/devgraph/native-read/v2/requests", json={}).status_code == 401
        for path in [
            "/work-operations/v1",
            "/credential-work/v2",
            "/monitor/access/",
            "/devgraph/access-operator/v1/actions",
            "/docs",
        ]:
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404
        assert (
            client.get(
                "/.well-known/devgraph-connection.json", headers={"Host": "evil.test"}
            ).status_code
            == 421
        )
        assert (
            client.get(
                "/.well-known/devgraph-connection.json", headers={"Origin": "https://evil.test"}
            ).status_code
            == 421
        )


def test_factory_keeps_discovery_identity_and_closes_admin_without_touching_policy(monkeypatch):
    from pathlib import Path

    import devgraph.lan_read_runtime as runtime
    import devgraph.signed_read_runtime as signed

    origin = "https://192.168.50.10:18891"
    values = {
        "schema": "devgraph.signed-read-runtime.v5",
        **{
            key: "/synthetic/" + key
            for key in (
                "member_policy_file",
                "access_inbox_database_file",
                "access_operator_policy_file",
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
        "secs_binary_sha256": "a" * 64,
    }
    authority = {
        "issuer": "test",
        "receiver": origin,
        "key_id": "test",
        "public_key": "b" * 64,
        "audience": "devgraph",
    }
    inputs = {
        Path("/synthetic/config"): values,
        Path(values["enrollment_policy_file"]): {"receiver": origin, "issuer": "test"},
        Path(values["authority_policy_file"]): authority,
    }
    app = FastAPI()
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", "/synthetic/config")
    monkeypatch.setenv("DEVGRAPH_LAN_ORIGIN", origin)
    monkeypatch.setattr(runtime, "private_json", inputs.__getitem__)
    monkeypatch.setattr(signed, "create_app", lambda: app)
    boundary = runtime.create_app()
    with TestClient(boundary, base_url=origin) as client:
        assert client.get("/.well-known/devgraph-connection.json").json()["authority"] == authority
        assert client.get("/live").json()["access_requires_current_grant"] is True
        assert client.get("/monitor/access/").status_code == 404
    assert app.state.access_operator_enabled is False
    inputs[Path(values["authority_policy_file"])] = {
        **authority,
        "receiver": "https://192.168.50.11",
    }
    with pytest.raises(ValueError, match="match the signed receiver"):
        runtime.create_app()
