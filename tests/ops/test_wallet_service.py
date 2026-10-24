import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.ops.test_owner_install import setup as setup

from devgraph.ops import owner_install as install
from devgraph.ops import wallet_service as service
from devgraph.wallet_service_runtime import create_unclaimed_app


class Port:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def bind(self, address):
        assert address[0] == "127.0.0.1"

    def getsockname(self):
        return ("127.0.0.1", 19443)


def prepared(s, monkeypatch):
    monkeypatch.setattr(
        install,
        "cli_read",
        lambda home, args: dict(
            healthy=True, api=dict(api=dict(base_url=s.plan["local_endpoint"]))
        ),
    )
    monkeypatch.setattr(service, "load_local_config", lambda _: s.config)
    monkeypatch.setattr(service, "code_manifest", lambda: (s.base / "immutable/source", "a" * 64))
    monkeypatch.setattr(service.socket, "socket", lambda *a, **kw: Port())
    return service.prepare(s.plan["resource_id"], s.plan["local_endpoint"], home=s.home, now=1000)


def test_prepare_binds_actual_graph_without_owner_or_native_changes(setup, monkeypatch):
    s = setup
    before = (s.base / "resource-registration.json").read_bytes()
    policy = (
        s.home
        / "Library/Application Support/Zenith/secS/authority/devgraph.work.v1/receiver-policy.json"
    )
    old = policy.read_bytes()
    out = prepared(s, monkeypatch)
    root = service.root_for(s.home, s.plan["resource_id"])
    d = out["review"]["deployment"]
    assert (
        d["source_endpoint"] == s.plan["local_endpoint"]
        and d["endpoint"] == "https://127.0.0.1:19443"
    )
    assert d["resource_id"] == s.plan["resource_id"] and d["instance_id"] != d["resource_id"]
    assert (
        out["review"]["owner_registered"] is False
        and out["review"]["administration_enabled"] is False
    )
    assert (
        s.base / "resource-registration.json"
    ).read_bytes() == before and policy.read_bytes() == old
    assert (root / "authority/read.key").stat().st_mode & 0o777 == 0o600
    assert not (s.base / "owners").exists() and not s.events
    with pytest.raises(ValueError, match="existing_service"):
        service.prepare(s.plan["resource_id"], s.plan["local_endpoint"], home=s.home, now=1001)


def test_unclaimed_service_has_metadata_but_no_graph_or_approval_routes(setup, monkeypatch):
    real_socket = service.socket.socket
    out = prepared(setup, monkeypatch)
    d = out["review"]["deployment"]
    monkeypatch.setattr(service.socket, "socket", real_socket)
    with TestClient(create_unclaimed_app(d), base_url=d["endpoint"]) as client:
        health = client.get("/live").json()
        assert (
            health["mode"] == "owner-local-wallet-unclaimed" and health["owner_registered"] is False
        )
        assert (
            health["administration_enabled"] is False and health["instance_id"] == d["instance_id"]
        )
        assert (
            client.get("/.well-known/devgraph-connection.json").json()["authority"]
            == d["connection"]["authority"]
        )
        assert (
            client.get("/.well-known/devgraph-wallet-service.json").json()["state"]
            == "owner_required"
        )
        for path in [
            "/devgraph/access-operator/v1/actions",
            "/devgraph/access-requests/v2/actions",
            "/devgraph/native-read/v2/enrollments",
            "/credential-work/v2/execute",
        ]:
            result = client.post(path, json={})
            assert result.status_code == 503 and result.json() == {"error": "owner_setup_required"}
        for path in ["/owner/claim", "/query", "/work/Task/private", "/local/config", "/docs"]:
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404
        assert client.get("/live", headers={"Host": "192.168.1.77:19443"}).status_code == 421
        assert client.get("/live", headers={"Authorization": "Bearer value"}).status_code == 421


def test_launch_is_exact_and_preserves_graph_and_authority(setup, monkeypatch):
    s = setup
    out = prepared(s, monkeypatch)
    d = out["review"]["deployment"]
    root = service.root_for(s.home, s.plan["resource_id"])
    original = (s.base / "resource-registration.json").read_bytes()
    calls = []

    def start(args, **kwargs):
        assert args[:2] == ["/bin/launchctl", "bootstrap"]
        assert (root / "state/launch-dispatch.json").exists()
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", start)
    monkeypatch.setattr(
        service,
        "probe",
        lambda root, d: dict(
            live=True,
            mode="owner-local-wallet-unclaimed",
            resource_id=d["resource_id"],
            instance_id=d["instance_id"],
            owner_registered=False,
            administration_enabled=False,
        ),
    )
    result = service.launch(s.plan["resource_id"], out["review_digest"], home=s.home)
    assert (
        result["running"]
        and result["member_grants_created"] == 0
        and result["native_policy_changed"] is False
    )
    assert len(calls) == 1 and (s.base / "resource-registration.json").read_bytes() == original
    import plistlib

    job = plistlib.loads((root / "launch-agent.plist").read_bytes())
    assert job["ProgramArguments"][job["ProgramArguments"].index("--host") + 1] == "127.0.0.1"
    assert not any("NEO4J" in k or "PASSWORD" in k for k in job["EnvironmentVariables"])
    record = json.loads(
        (s.home / ".zenith/services/v1" / (d["instance_id"] + ".json")).read_bytes()
    )
    assert record["endpoint"] == d["endpoint"] and record["resource_id"] == s.plan["resource_id"]
    with pytest.raises(ValueError, match="prior_launch"):
        service.launch(s.plan["resource_id"], out["review_digest"], home=s.home)
    assert len(calls) == 1 and not s.events


def test_ambiguous_dispatch_is_retained_and_never_retried(setup, monkeypatch):
    s = setup
    out = prepared(s, monkeypatch)
    calls = []

    def uncertain(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(service.subprocess, "run", uncertain)
    with pytest.raises(subprocess.TimeoutExpired):
        service.launch(s.plan["resource_id"], out["review_digest"], home=s.home)
    with pytest.raises(ValueError, match="prior_launch"):
        service.launch(s.plan["resource_id"], out["review_digest"], home=s.home)
    assert len(calls) == 1


def test_tampered_or_drifted_setup_never_dispatches(setup, monkeypatch):
    s = setup
    out = prepared(s, monkeypatch)
    root = service.root_for(s.home, s.plan["resource_id"])
    with pytest.raises(ValueError, match="launch_review_changed"):
        service.launch(s.plan["resource_id"], "0" * 64, home=s.home)
    (s.base / "local.json").write_text('{"instance":"changed"}')
    with pytest.raises(ValueError, match="source_graph_changed"):
        service.launch(s.plan["resource_id"], out["review_digest"], home=s.home)
    assert not (root / "state").exists()
