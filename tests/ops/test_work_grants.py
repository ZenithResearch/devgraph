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


@pytest.mark.parametrize("arenas", [False, True])
def test_workflow_grants_are_explicit_and_all_new_resources_are_bound(arenas):
    old = grants._policy(PUBLIC, 1, 99, 999, include_arenas=arenas)
    assert not grants._has_workflows(old)
    new = grants._policy(PUBLIC, 1, 99, 999, include_arenas=arenas, include_workflows=True)
    assert grants._validate_policy(new, PUBLIC) == (99, 999, 1)
    resources = {
        r["resource"] for r in new["rules"] if r["operation"] == "devgraph.work.workflow.review.v1"
    }
    assert resources == {
        k + "/" for k in (*grants.KINDS, "Decision", "ReviewPacket", "Handoff", "ExternalLink")
    }
    assert all(rule in new["rules"] for rule in old["rules"])
    broken = copy.deepcopy(new)
    broken["rules"].pop()
    with pytest.raises(grants.WorkGrantError):
        grants._validate_policy(broken, PUBLIC)


@pytest.mark.parametrize("arenas", [False, True])
def test_progress_grants_are_explicit_versioned_and_preserved_on_validation(arenas):
    old = grants._policy(PUBLIC, 1, 99, 999, include_arenas=arenas)
    assert not grants._has_progress(old)
    new = grants._policy(PUBLIC, 1, 99, 999, include_arenas=arenas, include_progress=True)
    assert grants._validate_policy(new, PUBLIC) == (99, 999, 1)
    assert not grants._has_workflows(new)
    rules = {(r["operation"], r["resource"]) for r in new["rules"]}
    assert ("devgraph.work.progress.set.v2", "Todo/") in rules
    assert ("devgraph.work.progress.set.v2", "ReviewPacket/") in rules
    assert ("devgraph.work.restore.v2", "Task/") in rules
    assert not any(op == "devgraph.work.status.v2" for op, _ in rules)
    assert not any(op.endswith(".v1") and resource == "Todo/" for op, resource in rules)
    assert ("devgraph.work.workflow.transition.v2", "Task/") in rules
    broken = {**new, "rules": new["rules"][:-1]}
    with pytest.raises(grants.WorkGrantError):
        grants._validate_policy(broken, PUBLIC)


@pytest.fixture
def shared_installation(installation, monkeypatch):
    from devgraph.auth.standing_access import StandingNativePolicy

    i = installation
    apply_new(i)
    old = copy.deepcopy(i.state["current"])
    policy = copy.deepcopy(old["policy"])
    policy.update(schema="secs-devgraph-work-policy.v2", schema_version=2, policy_version=2)
    for rule in policy["rules"]:
        start, end = rule.pop("not_before"), rule.pop("not_after")
        rule["validity"] = dict(
            schema="castalia.grant-validity.v1", mode="dated", not_before=start, not_after=end
        )
    foreign = dict(
        actor_id=grants._actor("33" * 32),
        effect="allow",
        status="active",
        operation="devgraph.work.patch.v2",
        resource="Task/",
        resource_match="prefix",
        validity=dict(schema="castalia.grant-validity.v1", mode="until_revoked", not_before=99),
    )
    deny = copy.deepcopy(foreign)
    deny.update(effect="deny", resource="Task/private-", resource_match="prefix")
    revoked = copy.deepcopy(foreign)
    revoked.update(status="revoked", resource="Project/")
    # The saved agent need not be the first identity in an explicitly shared policy.
    policy["rules"] = [foreign, deny, revoked] + policy["rules"]
    key_end = old["registry"]["keys"][0]["not_after"]

    def state_for(policy, end, key_id="test-secs-1"):
        result = copy.deepcopy(old)
        result.update(
            policy=copy.deepcopy(policy),
            policy_version=policy["policy_version"],
            policy_digest_sha256=grants._digest(policy),
            secs_verifier_key_id=key_id,
        )
        key = result["registry"]["keys"][0]
        key.update(not_after=end, key_id=key_id)
        result["registry_sha256"] = hashlib.sha256(grants._json(result["registry"])).hexdigest()
        result["key_current"] = key["not_before"] <= i.now[0] < end and key["status"] == "active"
        parsed = StandingNativePolicy.model_validate(policy)
        result["ready"] = result["key_current"] and any(
            r.status == "active" and r.effect == "allow" and r.validity.contains(i.now[0])
            for r in parsed.rules
        )
        return result

    i.state["current"] = state_for(policy, key_end)
    for version in (1, 2):
        directory = i.data / f"secrets/secs-magik/devgraph.work.v{version}"
        directory.mkdir(mode=0o700, exist_ok=True)
        for name, value in [
            ("receiver.json", grants._receiver_manifest(i.state["current"], version)),
            ("secs-public-key-registry.json", i.state["current"]["registry"]),
        ]:
            path = directory / name
            path.write_bytes(grants._json(value))
            path.chmod(0o600)

    def native(*args):
        i.state["calls"].append(args)
        if args[0] == "status":
            current = i.state["current"]
            return state_for(
                current["policy"],
                current["registry"]["keys"][0]["not_after"],
                current["secs_verifier_key_id"],
            )
        assert args[0] == "provision"
        proposed = json.loads(Path(args[args.index("--policy-file") + 1]).read_bytes())
        assert (
            args[args.index("--expected-policy-digest") + 1]
            == i.state["current"]["policy_digest_sha256"]
        )
        assert "--verifier-not-after" in args
        if all(r["status"] == "revoked" for r in proposed["rules"]):
            assert not (i.receiver / "receiver.json").exists()
        end = int(args[args.index("--verifier-not-after") + 1])
        key = (
            "test-secs-2"
            if "--rotate-verifier" in args
            else i.state["current"]["secs_verifier_key_id"]
        )
        i.state["current"] = state_for(proposed, end, key)
        return copy.deepcopy(i.state["current"])

    monkeypatch.setattr(grants, "_native", native)
    i.foreign = copy.deepcopy(policy["rules"][:3])
    i.native = native
    return i


def test_shared_status_tracks_saved_agent_instead_of_first_holder(shared_installation):
    i = shared_installation
    result = grants.grant_status()
    assert result["ready"] and result["actor_id"] == grants._actor(PUBLIC)
    assert result["operations_authorized"] == 11
    assert (
        result["expires_at"]
        == grants._managed_policy(i.state["current"]["policy"], PUBLIC)["rules"][0]["not_after"]
    )


def test_shared_agent_expiry_is_not_masked_by_standing_holder(shared_installation):
    i = shared_installation
    # Keep the service key valid and another holder current after agent expiry.
    end = i.state["current"]["registry"]["keys"][0]["not_after"]
    i.state["current"]["registry"]["keys"][0]["not_after"] = end + 3600
    i.now[0] = end
    assert i.native("status")["ready"]
    result = grants.grant_status()
    assert not result["ready"] and result["operations_authorized"] == 0
    assert (
        result["signer_matches"] and result["receiver_matches"] is False
    )  # changed key pin is independently denied
    # Match the reviewed key deadline; an expired agent still cannot use another holder's grant.
    current = i.native("status")
    for version in (1, 2):
        path = i.data / f"secrets/secs-magik/devgraph.work.v{version}/secs-public-key-registry.json"
        path.write_bytes(grants._json(current["registry"]))
        path.chmod(0o600)
    result = grants.grant_status()
    assert result["receiver_matches"] and not result["ready"]


def test_shared_renewal_preserves_other_holders_and_updates_both_pins(shared_installation):
    i = shared_installation
    plan = grants.plan_grant(24, renew=True)
    assert plan["schema"] == "devgraph.work-grant-plan.v2"
    assert grants._unmanaged_rules(plan["policy"], PUBLIC) == i.foreign
    path = i.path / "shared-plan.json"
    grants.write_plan(plan, path)
    result = grants.apply_grant(path)
    assert result["ready"] and result["policy_version"] == 3
    assert grants._unmanaged_rules(i.state["current"]["policy"], PUBLIC) == i.foreign
    assert i.state["current"]["secs_verifier_key_id"] == "test-secs-1"
    for version in (1, 2):
        manifest = json.loads(
            (i.data / f"secrets/secs-magik/devgraph.work.v{version}/receiver.json").read_bytes()
        )
        assert manifest["policy_binding"]["policy_digest_sha256"] == plan["policy_digest_sha256"]
    assert grants.rotate_verifier(24)["secs_verifier_key_id"] == "test-secs-2"
    assert grants._unmanaged_rules(i.state["current"]["policy"], PUBLIC) == i.foreign


@pytest.mark.parametrize(
    "change", ["foreign", "key_deadline", "key_extension", "stale", "schema", "initial"]
)
def test_shared_tampered_plan_never_dispatches(shared_installation, change):
    i = shared_installation
    plan = grants.plan_grant(24, renew=True)
    if change == "foreign":
        # Updating a self-consistent hash does not authorize changing a different holder.
        plan["policy"]["rules"][-3]["resource"] = "Project/"
        plan["policy_digest_sha256"] = grants._digest(plan["policy"])
        plan["preserved_rules_sha256"] = hashlib.sha256(
            grants._json(grants._unmanaged_rules(plan["policy"], PUBLIC))
        ).hexdigest()
    elif change == "key_deadline":
        plan["verifier_not_after"] = i.now[0] + 24 * 3600
    elif change == "key_extension":
        plan["verifier_not_after"] = 9007199254740991
    elif change == "stale":
        plan["previous_policy_digest_sha256"] = "0" * 64
    elif change == "initial":
        plan["previous_policy_digest_sha256"] = None
    else:
        plan["schema"] = grants.PLAN_SCHEMA
    before = sum(c[0] == "provision" for c in i.state["calls"])
    with pytest.raises(grants.WorkGrantError):
        grants._apply(plan)
    assert sum(c[0] == "provision" for c in i.state["calls"]) == before
    assert grants.grant_status()["ready"]


def test_shared_mismatched_v2_pin_holds_before_provision(shared_installation):
    i = shared_installation
    plan = grants.plan_grant(24, renew=True)
    path = i.data / "secrets/secs-magik/devgraph.work.v2/receiver.json"
    manifest = json.loads(path.read_bytes())
    manifest["policy_binding"]["policy_version"] = 999
    path.write_bytes(grants._json(manifest))
    path.chmod(0o600)
    before = sum(c[0] == "provision" for c in i.state["calls"])
    assert not grants.grant_status()["ready"]
    with pytest.raises(grants.WorkGrantError, match="binding reconciliation"):
        grants._apply(plan)
    assert sum(c[0] == "provision" for c in i.state["calls"]) == before


def test_shared_emergency_revocation_removes_receiver_before_all_holder_revocation(
    shared_installation,
):
    i = shared_installation
    result = grants.revoke_grant()
    assert result["producer_revoked"] and not result["ready"]
    assert all(r["status"] == "revoked" for r in i.state["current"]["policy"]["rules"])
    assert not (i.receiver / "receiver.json").exists()
    assert "--verifier-not-after" in i.state["calls"][-1] or i.state["calls"][-1][0] == "status"


def test_shared_partial_pin_update_preserves_original_plan_and_refuses_redispatch(
    shared_installation, monkeypatch
):
    i = shared_installation
    plan = grants.plan_grant(24, renew=True)
    original = grants._replace_at

    def fail(directory, name, value):
        if value.get("schema") == "devgraph-secs-work-receiver.v2":
            raise OSError("interrupted v2 receiver pin replacement")
        return original(directory, name, value)

    monkeypatch.setattr(grants, "_replace_at", fail)
    with pytest.raises(OSError):
        grants._apply(plan)
    assert i.state["current"]["policy_digest_sha256"] == plan["policy_digest_sha256"]
    assert not grants.grant_status()["ready"]
    before = sum(c[0] == "provision" for c in i.state["calls"])
    with pytest.raises(grants.WorkGrantError, match="binding reconciliation"):
        grants._apply(plan)
    assert sum(c[0] == "provision" for c in i.state["calls"]) == before
    assert grants._unmanaged_rules(i.state["current"]["policy"], PUBLIC) == i.foreign
