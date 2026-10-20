from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.lan_access_runtime import CREDENTIAL_PATHS, LanAccessBoundary

ORIGIN = "https://192.168.50.10:18891"


def test_explicit_access_listener_admits_only_guarded_routes_and_keeps_host_bounds():
    app = FastAPI()
    calls = []

    @app.post("/{path:path}", status_code=401)
    def guarded(path: str):
        calls.append(path)
        return {"error": "signature_required"}

    @app.get("/{path:path}")
    def get(path: str):
        return {"metadata": True}

    boundary = LanAccessBoundary(app, origin=ORIGIN)
    with TestClient(boundary, base_url=ORIGIN) as client:
        for path in [
            "/devgraph/access-operator/v1/actions",
            "/devgraph/access-requests/v2/actions",
            "/credential-work/v2/execute",
            "/todo-operations/v2",
        ]:
            assert client.post(path, json={}).status_code == 401
        assert client.get("/monitor/access/").status_code == 200
        assert client.get("/devgraph/access-requests/v2/catalog").status_code == 200
        assert client.get("/monitor/access-assets/policy.mjs").status_code == 200
        for path in [
            "/work-operations/v1",
            "/arena-operations/v2",
            "/sdk/work-operations/v1",
            "/work/Task/private",
            "/query",
            "/docs",
            "/openapi.json",
            "/local/config",
        ]:
            assert client.post(path, json={}).status_code == 404
            assert client.get(path).status_code == 404
        count = len(calls)
        for headers in [
            {"Host": "evil.example"},
            {"Origin": "https://evil.example"},
            {"Authorization": "Bearer arbitrary"},
        ]:
            assert (
                client.post("/credential-work/v2/execute", json={}, headers=headers).status_code
                == 421
            )
        assert client.post("/credential-work/v2/execute?x=1", json={}).status_code == 400
        assert client.post("/credential-work/v2/execute", content=b"x" * 262145).status_code == 413
        assert (
            client.post("/devgraph/access-operator/v1/actions", content=b"x" * 65537).status_code
            == 413
        )
        assert len(calls) == count
        assert not boundary.expired()
    assert boundary.body_limit(next(iter(CREDENTIAL_PATHS)), "POST") == 262144


@pytest.mark.parametrize(
    "origin",
    ["https://example.test", "https://8.8.8.8", "http://192.168.50.10", "https://127.0.0.1"],
)
def test_access_factory_cannot_become_public_or_cleartext(origin):
    with pytest.raises(ValueError):
        LanAccessBoundary(FastAPI(), origin=origin)


def test_factory_requires_v8_and_preserves_signed_identity(monkeypatch):
    import devgraph.lan_access_runtime as runtime
    import devgraph.signed_read_runtime as signed

    values = dict(
        schema="devgraph.signed-read-runtime.v8",
        membership_requirement="optional",
        secs_binary_sha256="a" * 64,
        **{
            k: "/synthetic/" + k
            for k in [
                "member_policy_file",
                "access_inbox_database_file",
                "access_operator_policy_file",
                "write_native_policy_file",
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            ]
        },
    )
    authority = dict(
        issuer="fixture",
        receiver=ORIGIN,
        key_id="fixture",
        public_key="b" * 64,
        audience="devgraph",
    )
    data = {
        Path("/synthetic/config"): values,
        Path(values["enrollment_policy_file"]): dict(receiver=ORIGIN, issuer="fixture"),
        Path(values["authority_policy_file"]): authority,
    }
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", "/synthetic/config")
    monkeypatch.setenv("DEVGRAPH_LAN_ORIGIN", ORIGIN)
    monkeypatch.setattr(runtime, "private_json", data.__getitem__)
    monkeypatch.setattr(signed, "create_app", lambda: FastAPI())
    with TestClient(runtime.create_app(), base_url=ORIGIN) as client:
        assert client.get("/.well-known/devgraph-connection.json").json()["authority"] == authority
        assert client.get("/live").json()["mode"] == "lan-standing-access"
    data[Path(values["authority_policy_file"])] = dict(authority, receiver="https://192.168.50.11")
    with pytest.raises(ValueError, match="match the signed receiver"):
        runtime.create_app()
    data[Path(values["authority_policy_file"])] = authority
    data[Path("/synthetic/config")] = {
        k: v
        for k, v in values.items()
        if k not in {"membership_requirement", "write_native_policy_file"}
    }
    data[Path("/synthetic/config")]["schema"] = "devgraph.signed-read-runtime.v5"
    with pytest.raises(ValueError, match="runtime v8"):
        runtime.create_app()
