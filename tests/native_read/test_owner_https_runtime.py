import copy
import json
import socket
from pathlib import Path
from socket import socket as real_socket

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from tests.native_read.test_owner_endpoint import publication
from tests.ops.test_owner_install import setup as setup

from devgraph.owner_endpoint import endpoint_operator_policy
from devgraph.owner_https_runtime import DedicatedTLSProxy, OwnerHTTPSBoundary
from devgraph.signed_read_runtime import ReadRuntimeConfiguration

ORIGINS = ("https://host.example.ts.net:9443", "https://graph.example.test")


def test_browser_asset_burst_does_not_consume_signed_request_capacity():
    import asyncio

    import httpx

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def slow(path: str):
        await asyncio.sleep(0.03)
        return {"path": path}

    async def exercise():
        boundary = OwnerHTTPSBoundary(app, origin=ORIGINS[0])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url=ORIGINS[0]
        ) as c:
            responses = await asyncio.gather(
                *(c.get(f"/monitor/topology-assets/asset-{i}.js") for i in range(24)),
                *(c.post("/devgraph/access-requests/v2/actions", json={}) for _ in range(9)),
            )
        assert all(r.status_code == 200 for r in responses[:24])
        assert [r.status_code for r in responses[24:]].count(200) == 8
        assert [r.status_code for r in responses[24:]].count(429) == 1

    asyncio.run(exercise())


def endpoint(s, monkeypatch, origin):
    config, profile = publication(s, monkeypatch)
    monkeypatch.setattr(socket, "socket", real_socket)
    profile = copy.deepcopy(profile)
    old = profile["receiver"]
    profile.update(receiver=origin, network="tailscale" if origin == ORIGINS[0] else None)
    profile.pop("tls_certificate_sha256")
    profile["authority"]["receiver"] = origin
    s.store(config.owner_endpoint_profile_file, profile)
    for field in ("enrollment_policy_file", "authority_policy_file", "access_operator_policy_file"):
        path = getattr(config, field)
        value = json.loads(path.read_text())
        selected = value["ceiling"] if field == "access_operator_policy_file" else value
        assert selected["receiver"] == old
        selected["receiver"] = origin
        s.store(path, value)
    return config, profile


@pytest.mark.parametrize("origin", ORIGINS)
def test_owner_binding_allows_explicit_tailnet_and_public_transports(setup, monkeypatch, origin):
    config, _ = endpoint(setup, monkeypatch, origin)
    assert endpoint_operator_policy(config, data_root=setup.data)["ceiling"]["receiver"] == origin


@pytest.mark.parametrize(
    "alias", ("cross-store", "parent-other-store", "symlink", "hardlink", "child")
)
def test_endpoint_state_cannot_alias_any_parent_or_endpoint_store(setup, monkeypatch, alias):
    config, _ = endpoint(setup, monkeypatch, ORIGINS[0])
    parent = json.loads(config.owner_parent_runtime_file.read_text())
    target = Path(parent["access_inbox_database_file"])
    setup.store(target, b"fixture")
    if alias == "cross-store":
        target = config.enrollment_database_file
    elif alias == "child":
        target = Path(parent["owner_native_admission_directory"]) / "inbox"
    elif alias in ("symlink", "hardlink"):
        link = config.access_inbox_database_file
        if alias == "symlink":
            link.symlink_to(target)
        else:
            link.hardlink_to(target)
        target = link
    changed = config.model_copy(update={"access_inbox_database_file": target})
    with pytest.raises(ValueError, match="state"):
        endpoint_operator_policy(changed, data_root=setup.data)


def test_proxy_mode_is_additive_and_never_allowed_on_original_owner_runtime(setup, monkeypatch):
    config, _ = endpoint(setup, monkeypatch, ORIGINS[0])
    parent = json.loads(config.owner_parent_runtime_file.read_text())
    parent["endpoint_tls_proxy_port"] = 65500
    with pytest.raises(ValueError, match="dedicated TLS proxy"):
        ReadRuntimeConfiguration.model_validate(parent)


def test_unclaimed_v9_preparation_cannot_authorize_a_secondary_endpoint(setup, monkeypatch):
    config, _ = endpoint(setup, monkeypatch, ORIGINS[0])
    parent = json.loads(config.owner_parent_runtime_file.read_text())
    parent.pop("owner_bootstrap_database_file")
    parent.pop("owner_install_review_file")
    setup.store(config.owner_parent_runtime_file, parent)
    with pytest.raises(ValueError, match="original signed owner"):
        endpoint_operator_policy(config, data_root=setup.data)


@pytest.mark.parametrize("origin", ORIGINS)
def test_fixed_loopback_termination_keeps_member_auth_and_operator_routes_closed(origin):
    app = FastAPI()

    @app.get("/live")
    def live(request: Request):
        return {"origin": str(request.base_url), "forwarded": request.headers.get("forwarded")}

    @app.post("/monitor/wallet-access/v1/enrollments", status_code=401)
    def enroll():
        return {"holder_approval_required": True}

    boundary = DedicatedTLSProxy(OwnerHTTPSBoundary(app, origin=origin), port=65500)
    with TestClient(boundary, base_url="http://127.0.0.1:65500", client=("127.0.0.1", 1234)) as c:
        headers = {"Host": origin.removeprefix("https://"), "Forwarded": "proto=https;host=evil"}
        response = c.get("/live", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"origin": origin + "/", "forwarded": None}
        assert (
            c.post("/monitor/wallet-access/v1/enrollments", json={}, headers=headers).status_code
            == 401
        )
        for path in (
            "/monitor/access/",
            "/devgraph/access-operator/v1/challenges",
            "/work-operations/v2",
        ):
            assert c.post(path, json={}, headers=headers).status_code == 404
        assert (
            c.get(
                "/live", headers={"Host": "evil.test", "X-Forwarded-Host": headers["Host"]}
            ).status_code
            == 421
        )
    for base, peer in (
        ("http://127.0.0.1:65501", "127.0.0.1"),
        ("http://0.0.0.0:65500", "127.0.0.1"),
        ("http://127.0.0.1:65500", "192.168.1.2"),
    ):
        with TestClient(boundary, base_url=base, client=(peer, 1234)) as c:
            assert c.get("/live", headers=headers).status_code == 421


@pytest.mark.parametrize("origin", ORIGINS)
def test_real_owner_composition_serves_metadata_and_rejects_unsigned_requests(
    setup, monkeypatch, origin
):
    from types import SimpleNamespace

    import devgraph.signed_read_runtime as runtime
    from devgraph.auth.write_provisioning import NativeWritePolicy
    from devgraph.ops.named_work_v2_receiver import LocalNamedWorkV2Receiver
    from devgraph.owner_https_runtime import create_app
    from devgraph.storage.memory import MemoryGraphStorage

    config, _ = endpoint(setup, monkeypatch, origin)
    parent = json.loads(config.owner_parent_runtime_file.read_text())
    members = json.loads(Path(parent["member_policy_file"]).read_text())
    members["ceiling"]["receiver"] = origin
    members["assignments"] = []
    setup.store(config.member_policy_file, members)
    path = setup.store(
        setup.base / "https-runtime.json",
        config.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    monkeypatch.setattr(Path, "home", lambda: setup.home)
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", str(path))
    monkeypatch.setenv("DEVGRAPH_DATA_ROOT", str(setup.data))
    monkeypatch.setenv("DEVGRAPH_ENDPOINT_ORIGIN", origin)
    storage = MemoryGraphStorage()
    storage.close = lambda: None
    monkeypatch.setattr(runtime, "Neo4jGraphStorage", lambda _: storage)
    monkeypatch.setattr(runtime.Neo4jConfig, "from_env", lambda: None)
    native = NativeWritePolicy.model_validate(
        json.loads(config.write_native_policy_file.read_text())
    )

    def require_binding(*binding):
        assert binding == (native.policy_id, native.policy_version, native.digest)

    # This fixture has no native executable/receiver. The composition must still
    # bind the exact native policy; live deployment checks the real verifier.
    monkeypatch.setattr(
        LocalNamedWorkV2Receiver,
        "_load_verifier",
        lambda _: SimpleNamespace(
            config=SimpleNamespace(policy_registry=SimpleNamespace(require_binding=require_binding))
        ),
    )
    with TestClient(create_app(), base_url=origin) as c:
        assert c.get("/live").json()["credential_requests_enabled"] is True
        metadata = c.get("/monitor/wallet-access/v1")
        assert metadata.status_code == 200
        assert metadata.json()["access_requests_endpoint"] == "/devgraph/access-requests/v2"
        assert c.get("/.well-known/devgraph-connection.json").json()["receiver"] == origin
        assert c.get("/").status_code == 200
        assert c.get("/monitor/topology-assets/wallet-access.js").status_code == 200
        assert c.post("/monitor/wallet-access/v1/enrollments", json={}).status_code in (400, 401)
        assert c.post("/devgraph/access-requests/v2/actions", json={}).status_code in (400, 401)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from tests.native_read.test_access_operator import signed
        from tests.native_read.test_access_requests import action

        holder = Ed25519PrivateKey.generate()
        public = holder.public_key().public_bytes_raw().hex()
        request = action(public).model_dump(mode="json", by_alias=True)
        ch = c.post("/devgraph/access-requests/v2/challenges", json={"request": request})
        assert ch.status_code == 200
        challenge = ch.json()
        approval = dict(challenge=challenge, proof={}, signature=signed(holder, challenge))
        result = c.post("/devgraph/access-requests/v2/actions", json=approval)
        assert result.status_code == 200
        assert result.json()["status"] == "pending"
        assert result.json()["authority_issued"] is False
        assert c.post("/devgraph/access-requests/v2/actions", json=approval).json() == result.json()
        assert c.get("/monitor/snapshot").status_code == 404
        assert c.post("/work-operations/v2", json={}).status_code == 404
