import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.ops.test_owner_install import setup as setup

from devgraph.auth.owner_bootstrap import OWNER_DOMAIN, canonical
from devgraph.ops import owner_install as m
from devgraph.ops import wallet_admin_upgrade as upgrade


def local_plan(s, monkeypatch, key=None):
    s.plan.update(
        schema="zenith.devgraph-owner-setup.v3",
        wallet_endpoint="https://127.0.0.1:19231",
        preset="project-manager",
        write_operations=list(upgrade.OWNER_OPERATIONS),
    )
    if key:
        s.plan["owner_public_key"] = (
            key.public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            .hex()
        )
    s.store(s.plan_file, s.plan)
    old = m.cli_read
    monkeypatch.setattr(
        m,
        "cli_read",
        lambda home, args: (
            dict(healthy=True, api=dict(api=dict(base_url=s.plan["local_endpoint"])))
            if args == ["local", "status"]
            else old(home, args)
        ),
    )
    return m.prepare(s.plan_file, home=s.home, now=1000)


def test_actual_graph_upgrade_is_loopback_only_and_preserves_source(setup, monkeypatch):
    s = setup
    out = local_plan(s, monkeypatch)
    review = out["review"]
    assert review["source_endpoint"] == "http://127.0.0.1:8080"
    assert review["endpoint"] == "https://127.0.0.1:19231"
    assert review["connection"]["network"] == "local"
    assert review["owner_claim_required"] is True and review["local_only"] is True
    config = json.loads((Path(review["root"]) / "runtime.json").read_bytes())
    assert config["owner_bootstrap_database_file"] == str(
        upgrade.registry_path(s.home, s.plan["resource_id"])
    )
    assert config["owner_install_review_file"] == str(Path(review["root"]) / "review.json")
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy

    runtime = ReadRuntimeConfiguration.model_validate(config)
    with pytest.raises(ValueError):
        owner_operator_policy(runtime, data_root=s.data)
    assert not upgrade.registry_path(s.home, s.plan["resource_id"]).exists()
    with pytest.raises(m.OwnerInstallError, match="owner_registration_required"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)
    assert not (Path(review["root"]) / "state/activation-started.json").exists()
    assert not any(e[2] == "provision" for e in s.events)


def test_wallet_claim_is_bound_to_exact_upgrade_and_consumed_once(setup, monkeypatch):
    key = Ed25519PrivateKey.generate()
    s = setup
    out = local_plan(s, monkeypatch, key)
    challenge = upgrade.prepare_claim(s.plan_file, out["review_digest"], home=s.home, now=1001)
    assert challenge["admin_grant"]["grant_id"] == out["review_digest"]
    assert challenge["binding"]["endpoint"] == s.plan["local_endpoint"]
    signed = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=challenge,
        signature=key.sign(OWNER_DOMAIN + canonical(challenge)).hex(),
    )
    proof_file = s.store(s.base / "owner-proof.json", signed)
    result = upgrade.complete_claim(
        s.plan_file, out["review_digest"], proof_file, home=s.home, now=1002
    )
    assert result["active"] and not result["duplicate"]
    before = upgrade.registry_path(s.home, s.plan["resource_id"]).read_bytes()
    assert upgrade.inspect_claim(s.plan_file, out["review_digest"], proof_file, home=s.home)[
        "duplicate"
    ]
    assert before == upgrade.registry_path(s.home, s.plan["resource_id"]).read_bytes()
    # Later legitimate native admission may change a staged policy; original
    # owner status is historical verification, not an activation retry.
    native_file = Path(out["review"]["root"]) / "authority/native-policy.json"
    native_original = native_file.read_bytes()
    native_file.write_bytes(b"native policy changed after grant activation")
    assert upgrade.inspect_claim(s.plan_file, out["review_digest"], proof_file, home=s.home)[
        "duplicate"
    ]
    native_file.write_bytes(native_original)
    altered = dict(signed, signature="0" * 128)
    changed_proof = s.store(s.base / "different-proof.json", altered)
    with pytest.raises(ValueError):
        upgrade.inspect_claim(s.plan_file, out["review_digest"], changed_proof, home=s.home)
    assert not upgrade.token_path(s.home, s.plan["resource_id"]).exists()
    assert upgrade.complete_claim(
        s.plan_file, out["review_digest"], proof_file, home=s.home, now=1003
    )["duplicate"]
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy

    runtime = ReadRuntimeConfiguration.model_validate(
        json.loads((Path(out["review"]["root"]) / "runtime.json").read_bytes())
    )
    policy = owner_operator_policy(runtime, data_root=s.data)
    assert policy["operator_public_keys"] == [s.plan["owner_public_key"]]
    with pytest.raises(ValueError):
        owner_operator_policy(runtime, data_root=s.data / "different-graph")
    grant = upgrade.require_owner(s.plan_file, out["review_digest"], home=s.home)
    assert grant["holder_public_key"] == s.plan["owner_public_key"]
    with pytest.raises(ValueError):
        upgrade.prepare_claim(s.plan_file, out["review_digest"], home=s.home, now=1004)


def test_changed_owner_or_review_cannot_activate(setup, monkeypatch):
    key = Ed25519PrivateKey.generate()
    s = setup
    out = local_plan(s, monkeypatch, key)
    challenge = upgrade.prepare_claim(s.plan_file, out["review_digest"], home=s.home, now=1001)
    other = Ed25519PrivateKey.generate()
    signed = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=challenge,
        signature=other.sign(OWNER_DOMAIN + canonical(challenge)).hex(),
    )
    proof_file = s.store(s.base / "owner-proof.json", signed)
    with pytest.raises(ValueError):
        upgrade.complete_claim(s.plan_file, out["review_digest"], proof_file, home=s.home, now=1002)
    with pytest.raises(ValueError):
        upgrade.require_owner(s.plan_file, out["review_digest"], home=s.home)
    assert upgrade.token_path(s.home, s.plan["resource_id"]).exists()
    assert not any(e[2] == "provision" for e in s.events)


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:19231",
        "https://localhost:19231",
        "https://192.168.1.77:19231",
        "https://0.0.0.0:19231",
    ],
)
def test_local_upgrade_cannot_become_a_network_listener(setup, monkeypatch, origin):
    s = setup
    s.plan.update(schema="zenith.devgraph-owner-setup.v3", wallet_endpoint=origin)
    s.store(s.plan_file, s.plan)
    with pytest.raises(ValueError):
        m.prepare(s.plan_file, home=s.home, now=1000)
    assert not (s.base / "wallet-endpoints").exists()


def authorize_local_upgrade(s, monkeypatch):
    key = Ed25519PrivateKey.generate()
    out = local_plan(s, monkeypatch, key)
    challenge = upgrade.prepare_claim(s.plan_file, out["review_digest"], home=s.home, now=1001)
    signed = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=challenge,
        signature=key.sign(OWNER_DOMAIN + canonical(challenge)).hex(),
    )
    proof_file = s.store(s.base / "owner-proof.json", signed)
    upgrade.complete_claim(s.plan_file, out["review_digest"], proof_file, home=s.home, now=1002)
    return out


def test_unsigned_legacy_installation_is_not_an_owner_upgrade(setup):
    s = setup
    out = m.prepare(s.plan_file, home=s.home, now=1000)
    with pytest.raises(m.OwnerInstallError, match="signed_owner_upgrade_required"):
        m.activate(s.plan_file, out["review_digest"], home=s.home)
    assert not any(e[2] == "provision" for e in s.events)


def test_runtime_rechecks_owner_and_exact_operator_policy(setup, monkeypatch):
    import sqlite3

    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy

    s = setup
    out = authorize_local_upgrade(s, monkeypatch)
    root = Path(out["review"]["root"])
    runtime = ReadRuntimeConfiguration.model_validate(
        json.loads((root / "runtime.json").read_bytes())
    )
    operator_file = runtime.access_operator_policy_file
    original = operator_file.read_bytes()
    changed = json.loads(original)
    changed["operator_public_keys"].append("a" * 64)
    operator_file.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        owner_operator_policy(runtime, data_root=s.data)
    operator_file.write_bytes(original)
    assert owner_operator_policy(runtime, data_root=s.data)["operator_public_keys"] == [
        s.plan["owner_public_key"]
    ]
    with sqlite3.connect(upgrade.registry_path(s.home, s.plan["resource_id"])) as db:
        state = json.loads(
            db.execute("SELECT document FROM bootstrap WHERE singleton=1").fetchone()[0]
        )
        state["state"] = "revoked"
        db.execute("UPDATE bootstrap SET document=? WHERE singleton=1", (json.dumps(state),))
    with pytest.raises(ValueError):
        owner_operator_policy(runtime, data_root=s.data)


def test_refresh_launched_service_is_metadata_only_and_never_retargets(setup, monkeypatch):
    s = setup
    monkeypatch.setattr(
        m,
        "cli_read",
        lambda home, args: dict(
            healthy=True, api=dict(api=dict(base_url=s.plan["local_endpoint"]))
        ),
    )
    original = (s.base / "resource-registration.json").read_bytes()
    result = upgrade.refresh_service(s.plan["resource_id"], s.plan["local_endpoint"], home=s.home)
    assert result["authority_granted"] is False and result["graph_registration_changed"] is False
    assert (s.base / "resource-registration.json").read_bytes() == original
    with pytest.raises(ValueError):
        upgrade.refresh_service(s.plan["resource_id"], "https://127.0.0.1:19443", home=s.home)
    assert not upgrade.registry_path(s.home, s.plan["resource_id"]).exists()
    assert not s.events
