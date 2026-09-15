"""Managed Work grants: cross-component pins, review plans, and fail-closed cutovers."""

import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devgraph.ops import signer_profile
from devgraph.ops import work_grants as grants

PUBLIC = "11" * 32


def descriptor(policy, now, key_id="test-secs-1"):
    registry = {
        "schema": "secs-public-verifier-key-registry.v1",
        "schema_version": 1,
        "keys": [
            {
                "algorithm": "ed25519",
                "key_id": key_id,
                "public_key_base64url": base64.urlsafe_b64encode(bytes.fromhex("22" * 32))
                .decode()
                .rstrip("="),
                "production_authority": True,
                "status": "active",
                "not_before": 99,
                "not_after": policy["rules"][0]["not_after"],
            }
        ],
    }
    start, end = policy["rules"][0]["not_before"], policy["rules"][0]["not_after"]
    return {
        "schema": "secs-devgraph-work-admin.v1",
        "action": "status",
        "ready": start <= now < end and policy["rules"][0]["status"] == "active",
        "key_current": now < end,
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_digest_sha256": grants._digest(policy),
        "secs_verifier_key_id": key_id,
        "registry_sha256": hashlib.sha256(grants._json(registry)).hexdigest(),
        "policy": policy,
        "registry": registry,
        "files": [],
    }


@pytest.fixture
def installation(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    root = tmp_path / "install"
    root.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    (data / "secrets").mkdir(mode=0o700)
    monkeypatch.setattr(signer_profile, "INSTALL_ROOT", root)
    monkeypatch.setattr(
        grants,
        "load_local_config",
        lambda _: SimpleNamespace(data_root=data, availability_path=data),
    )
    monkeypatch.setattr(grants, "_signer", lambda: PUBLIC)
    monkeypatch.setattr(signer_profile, "signer_status", lambda **_: {"public_key": PUBLIC})
    now = [2_000_000_000]
    monkeypatch.setattr(grants.time, "time", lambda: now[0])
    state = {"current": None, "calls": []}
    receiver = data / "secrets/secs-magik/devgraph.work.v1"

    def native(*args):
        state["calls"].append(args)
        if args[0] == "status":
            if state["current"] is None:
                return {
                    "schema": "secs-devgraph-work-admin.v1",
                    "action": "status",
                    "ready": False,
                    "state": "missing",
                }
            current = copy.deepcopy(state["current"])
            return descriptor(current["policy"], now[0], current["secs_verifier_key_id"])
        assert args[0] == "provision"
        policy = json.loads(Path(args[2]).read_bytes())
        if policy["rules"][0]["status"] == "revoked":
            assert not (receiver / "receiver.json").exists(), (
                "revocation must remove receiver admission first"
            )
        key_id = "test-secs-2" if "--rotate-verifier" in args else "test-secs-1"
        state["current"] = descriptor(policy, now[0], key_id)
        return copy.deepcopy(state["current"])

    monkeypatch.setattr(grants, "_native", native)
    return SimpleNamespace(
        root=root, data=data, receiver=receiver, now=now, state=state, native=native, path=tmp_path
    )


def apply_new(installation):
    plan = grants.plan_grant()
    path = installation.path / "grant.json"
    grants.write_plan(plan, path)
    return plan, grants.apply_grant(path)


def test_arena_grants_require_explicit_extension_and_survive_renewal(installation):
    apply_new(installation)
    assert grants.renew_grant()["operations_authorized"] == 11
    plan = grants.plan_grant(renew=True, include_arenas=True)
    rules = plan["policy"]["rules"]
    assert len(rules) == 48
    assert {(r["operation"], r["resource"]) for r in rules if "arena" in r["operation"]} == {
        (f"devgraph.arena.{operation}.v1", f"{kind}/")
        for operation, kinds in {
            "create": ("Arena",),
            "patch": ("Arena",),
            "archive": ("Arena",),
            "member.set": ("Arena", "Initiative", "Task"),
        }.items()
        for kind in kinds
    }
    assert any(
        r["operation"] == "devgraph.work.parent.set.v1" and r["resource"] == "Arena/" for r in rules
    )
    # Planning cannot widen the installed authority.
    assert grants.grant_status()["operations_authorized"] == 11
    path = installation.path / "arena-grant.json"
    grants.write_plan(plan, path)
    status = grants.apply_grant(path)
    assert status["operations_authorized"] == 15
    assert status["arena_operations_authorized"] == 4
    assert grants.renew_grant()["operations_authorized"] == 15
    assert grants.rotate_verifier()["operations_authorized"] == 15
    assert grants.revoke_grant()["producer_revoked"]
    assert len(installation.state["current"]["policy"]["rules"]) == 48


def test_arena_policy_rejects_partial_or_broader_grants():
    policy = grants._policy(PUBLIC, 1, 99, 200, include_arenas=True)
    assert grants._validate_policy(policy, PUBLIC) == (99, 200, 1)
    for mutate in (
        lambda rules: rules.pop(),
        lambda rules: rules[-1].update(resource="Project/"),
        lambda rules: rules.append(dict(rules[-1])),
    ):
        invalid = copy.deepcopy(policy)
        mutate(invalid["rules"])
        with pytest.raises(grants.WorkGrantError):
            grants._validate_policy(invalid, PUBLIC)


def test_plan_exact_rules_and_native_receiver_activation(installation):
    plan, status = apply_new(installation)
    assert len(plan["policy"]["rules"]) == 41
    assert len({r["operation"] for r in plan["policy"]["rules"]}) == 11
    assert (
        plan["policy"]["rules"][0]["actor_id"]
        == "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(PUBLIC)).hexdigest()
    )
    assert status["ready"] and status["operations_authorized"] == 11
    assert (installation.receiver / "receiver.json").stat().st_mode & 0o777 == 0o600
    assert (installation.path / "grant.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(grants.WorkGrantError, match="already exists"):
        grants.write_plan(plan, installation.path / "grant.json")
    with pytest.raises(grants.WorkGrantError, match="already been applied"):
        grants.apply_grant(installation.path / "grant.json")
    assert len([c for c in installation.state["calls"] if c[0] == "provision"]) == 1


def test_renew_rotate_revoke_and_no_automatic_renew(installation):
    apply_new(installation)
    with pytest.raises(grants.WorkGrantError, match="explicit renew"):
        grants.plan_grant()
    renewed = grants.renew_grant(24)
    assert renewed["policy_version"] == 2 and renewed["secs_verifier_key_id"] == "test-secs-1"
    rotated = grants.rotate_verifier(24)
    assert rotated["policy_version"] == 3 and rotated["secs_verifier_key_id"] == "test-secs-2"
    revoked = grants.revoke_grant()
    assert not revoked["ready"] and revoked["operations_authorized"] == 0
    assert not (installation.receiver / "receiver.json").exists()
    assert installation.state["current"]["policy"]["rules"][0]["status"] == "revoked"
    assert (
        json.loads((installation.receiver / "secs-public-key-registry.json").read_bytes())
        == installation.state["current"]["registry"]
    )


def test_receiver_failure_can_complete_same_reviewed_plan(installation, monkeypatch):
    plan = grants.plan_grant()
    path = installation.path / "grant.json"
    grants.write_plan(plan, path)
    original = grants._replace_at

    def fail(directory, name, value):
        if name == "receiver.json":
            raise OSError("simulated interruption")
        return original(directory, name, value)

    monkeypatch.setattr(grants, "_replace_at", fail)
    with pytest.raises(grants.WorkGrantError, match="could not complete"):
        grants.apply_grant(path)
    assert not grants.grant_status()["ready"]
    monkeypatch.setattr(grants, "_replace_at", original)
    assert grants.apply_grant(path)["ready"]
    assert len([c for c in installation.state["calls"] if c[0] == "provision"]) == 1


@pytest.mark.parametrize(
    "change", ["actor", "scope", "digest", "version", "schema", "time", "unknown"]
)
def test_modified_stale_or_broadened_plan_does_not_provision(installation, change):
    plan = grants.plan_grant()
    if change == "actor":
        plan["public_key"] = "33" * 32
    if change == "scope":
        plan["policy"]["rules"][0]["resource"] = "Decision/"
    if change == "digest":
        plan["policy_digest_sha256"] = "0" * 64
    if change == "version":
        plan["policy"]["policy_version"] = True
    if change == "schema":
        plan["schema"] = "unknown"
    if change == "time":
        installation.now[0] += 901
    if change == "unknown":
        plan["surprise"] = "not allowed"
    with pytest.raises(grants.WorkGrantError):
        grants._apply(plan)
    assert not any(c[0] == "provision" for c in installation.state["calls"])


@pytest.mark.parametrize("change", ["digest", "key", "policy", "expiry", "receiver", "signer"])
def test_status_detects_inactive_or_inconsistent_authority(installation, monkeypatch, change):
    apply_new(installation)
    original = installation.native

    def invalid(*args):
        result = original(*args)
        if change == "digest":
            result["registry_sha256"] = "0" * 64
        if change == "key":
            result["key_current"] = False
        if change == "policy":
            result["policy"]["rules"].pop()
        return result

    monkeypatch.setattr(grants, "_native", invalid)
    if change == "expiry":
        installation.now[0] += 720 * 3600
    if change == "receiver":
        (installation.receiver / "receiver.json").write_text("{}")
    if change == "signer":
        monkeypatch.setattr(signer_profile, "signer_status", lambda **_: {"public_key": "44" * 32})
    assert not grants.grant_status()["ready"]


def test_plan_output_refuses_symlink_parent_and_broad_private_leaf(installation):
    plan = grants.plan_grant()
    link = installation.path / "link"
    link.symlink_to(installation.path, target_is_directory=True)
    with pytest.raises(grants.WorkGrantError):
        grants.write_plan(plan, link / "plan.json")
    broad = installation.path / "broad"
    broad.mkdir(mode=0o755)
    with pytest.raises(grants.WorkGrantError):
        grants.write_plan(plan, broad / "plan.json")


def test_stale_previous_binding_does_not_mutate(installation):
    plan, _ = apply_new(installation)
    grants.renew_grant()
    with pytest.raises(grants.WorkGrantError, match="stale"):
        grants._apply(plan)


@pytest.mark.parametrize("ttl", [0, -1, True, 8761, "720"])
def test_invalid_lifetimes_rejected_before_key_or_service_calls(installation, ttl):
    with pytest.raises(grants.WorkGrantError, match="lifetime"):
        grants.plan_grant(ttl)
    assert installation.state["calls"] == []


@pytest.mark.parametrize("lost", ["key", "profile", "producer"])
def test_emergency_revoke_survives_lost_identity_and_producer(installation, monkeypatch, lost):
    apply_new(installation)

    def cannot_sign():
        raise grants.WorkGrantError("actor unavailable")

    monkeypatch.setattr(grants, "_signer", cannot_sign)
    monkeypatch.setattr(signer_profile, "signer_status", lambda **_: {"configured": False})
    if lost == "producer":

        def cannot_produce(*_):
            assert not (installation.receiver / "receiver.json").exists()
            raise grants.WorkGrantError("producer unavailable")

        monkeypatch.setattr(grants, "_native", cannot_produce)
    result = grants.revoke_grant()
    assert result["receiver_revoked"]
    assert result["producer_revoked"] is (lost != "producer")
    assert not (installation.receiver / "receiver.json").exists()
    assert not result["ready"]
