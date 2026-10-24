import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.ops.test_owner_install import setup as setup
from tests.ops.test_wallet_service import prepared

from devgraph.auth.owner_bootstrap import OWNER_DOMAIN, canonical
from devgraph.ops import owner_install as install
from devgraph.ops import service_owner as owner
from devgraph.ops import wallet_admin_upgrade as upgrade
from devgraph.ops import wallet_service as service


def running(s, monkeypatch):
    before_cli = install.cli_read
    launch = prepared(s, monkeypatch)
    monkeypatch.setattr(
        install,
        "cli_read",
        lambda home, args: (
            dict(healthy=True, api=dict(api=dict(base_url=s.plan["local_endpoint"])))
            if args == ["local", "status"]
            else before_cli(home, args)
        ),
    )
    d = launch["review"]["deployment"]
    monkeypatch.setattr(service, "probe", lambda *args: dict(live=True))
    monkeypatch.setattr(
        service.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    service.launch(d["resource_id"], launch["review_digest"], home=s.home)
    return d


def preparation(s, monkeypatch):
    d = running(s, monkeypatch)
    key = Ed25519PrivateKey.generate()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    result = owner.prepare(
        d["resource_id"], d["instance_id"], d["endpoint"], public, home=s.home, now=1000
    )
    return d, key, result


def authorize(s, monkeypatch):
    d, key, result = preparation(s, monkeypatch)
    path = owner.plan_path(s.home, d["resource_id"])
    digest = result["preparation"]["review_digest"]
    challenge = upgrade.prepare_claim(path, digest, home=s.home, now=1001)
    assert challenge["binding"]["endpoint"] == d["endpoint"]
    proof = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=challenge,
        signature=key.sign(OWNER_DOMAIN + canonical(challenge)).hex(),
    )
    proof_file = s.store(s.base / "original-owner-proof.json", proof)
    upgrade.complete_claim(path, digest, proof_file, home=s.home, now=1002)
    return d, path, result


def test_prepare_reuses_exact_running_identity_certificate_and_issuer(setup, monkeypatch):
    s = setup
    d, key, result = preparation(s, monkeypatch)
    review = result["preparation"]["review"]
    root = Path(review["root"])
    service_root = service.root_for(s.home, d["resource_id"])
    assert review["connection"] == d["connection"]
    for relative in ("tls/tls.crt", "tls/tls.key", "authority/read.key"):
        assert (root / relative).read_bytes() == (service_root / relative).read_bytes()
    assert review["existing_service"]["instance_id"] == d["instance_id"]
    assert result["plan"]["wallet_endpoint"] == d["endpoint"]
    assert not upgrade.registry_path(s.home, d["resource_id"]).exists()
    assert all(row[2] == "status" for row in s.events)
    state = owner.inspect(d["resource_id"], d["instance_id"], d["endpoint"], home=s.home)
    assert state["prepared"] and not state["owner_registered"] and not state["active"]
    with pytest.raises(ValueError, match="inspect_retained"):
        owner.prepare(
            d["resource_id"],
            d["instance_id"],
            d["endpoint"],
            result["plan"]["owner_public_key"],
            home=s.home,
        )
    destination = os.environ.get("SERVICE_OWNER_FIXTURE")
    if destination:
        Path(destination).write_bytes(
            install.encoded(
                dict(
                    service=dict(
                        schema="zenith.local-service.v1",
                        kind="devgraph",
                        interface="wallet-api",
                        name=d["name"],
                        endpoint=d["endpoint"],
                        connection=d["connection"],
                        instance_id=d["instance_id"],
                        resource_id=d["resource_id"],
                        updated_at=1000,
                    ),
                    **result,
                )
            )
        )


def test_wrong_selection_or_changed_job_never_creates_owner_setup(setup, monkeypatch):
    s = setup
    d = running(s, monkeypatch)
    for ident, endpoint in [
        ("other", d["endpoint"]),
        (d["instance_id"], "https://127.0.0.1:19999"),
    ]:
        with pytest.raises(ValueError, match="selected_service"):
            owner.prepare(d["resource_id"], ident, endpoint, "11" * 32, home=s.home)
    plist = (
        s.home
        / "Library/LaunchAgents"
        / ("ca.zenith.devgraph.wallet-service." + d["instance_id"] + ".plist")
    )
    plist.write_bytes(b"different-job")
    with pytest.raises(ValueError, match="selected_job"):
        owner.prepare(d["resource_id"], d["instance_id"], d["endpoint"], "11" * 32, home=s.home)
    assert not owner.plan_path(s.home, d["resource_id"]).exists()


def test_unsigned_same_service_cannot_activate(setup, monkeypatch):
    s = setup
    d, _, result = preparation(s, monkeypatch)
    with pytest.raises(ValueError, match="owner_registration_required"):
        owner.dispatch(
            "activate",
            d["resource_id"],
            d["instance_id"],
            d["endpoint"],
            result["preparation"]["review_digest"],
            home=s.home,
        )
    assert not any(row[2] == "provision" for row in s.events)


def test_activation_replaces_only_selected_job_and_preserves_service_profile(setup, monkeypatch):
    import http.client

    s = setup
    original = (s.base / "resource-registration.json").read_bytes()
    d, path, result = authorize(s, monkeypatch)
    review = result["preparation"]["review"]
    calls = []

    def launch(args, **kwargs):
        calls.append(args)
        assert args[0] == "/bin/launchctl" and args[1] in ("bootout", "bootstrap")
        if args[1] == "bootout":
            assert args[2].endswith("/ca.zenith.devgraph.wallet-service." + d["instance_id"])
            assert (Path(review["root"]) / "state/unclaimed-cutover-started.json").exists()
        return SimpleNamespace(returncode=0)

    class Response:
        status = 200

        def __init__(self, path):
            self.path = path

        def read(self, *_):
            if self.path == "/live":
                return install.encoded(
                    dict(
                        live=True,
                        mode="owner-local-wallet-administration",
                        instance_id=d["instance_id"],
                        resource_id=d["resource_id"],
                    )
                )
            return install.encoded(
                {
                    k: v
                    for k, v in d["connection"].items()
                    if k not in ("network", "tls_certificate_sha256")
                }
            )

    class Connection:
        def __init__(self, *_, **kwargs):
            self.path = None

        def request(self, method, path):
            self.path = path

        def getresponse(self):
            return Response(self.path)

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(install.subprocess, "run", launch)
    out = install.activate(path, result["preparation"]["review_digest"], home=s.home)
    assert out["active"] and out["connection"] == d["connection"]
    assert [row[1] for row in calls] == ["bootout", "bootstrap"]
    assert (s.base / "resource-registration.json").read_bytes() == original
    published = json.loads(
        (s.home / ".zenith/services/v1" / (d["instance_id"] + ".json")).read_bytes()
    )
    assert (
        published["instance_id"] == d["instance_id"] and published["connection"] == d["connection"]
    )
    assert not upgrade.token_path(s.home, d["resource_id"]).exists()
    checked = owner.inspect(d["resource_id"], d["instance_id"], d["endpoint"], home=s.home)
    assert checked["active"] and checked["owner_registered"]
    with pytest.raises(ValueError, match="prior_activation"):
        install.activate(path, result["preparation"]["review_digest"], home=s.home)
    assert len(calls) == 2


def test_uncertain_same_job_cutover_is_retained_without_redispatch(setup, monkeypatch):
    s = setup
    d, path, result = authorize(s, monkeypatch)
    calls = []

    def fail_stop(args, **kwargs):
        calls.append(args)
        assert args[1] == "bootout"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(install.subprocess, "run", fail_stop)
    digest = result["preparation"]["review_digest"]
    with pytest.raises(ValueError, match="stop_requires_inspection"):
        install.activate(path, digest, home=s.home)
    state = owner.inspect(d["resource_id"], d["instance_id"], d["endpoint"], home=s.home)
    assert state["owner_registered"] and state["activation_attempted"] and not state["active"]
    with pytest.raises(ValueError, match="prior_activation"):
        install.activate(path, digest, home=s.home)
    assert len(calls) == 1
