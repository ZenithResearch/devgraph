import hashlib
import json
import secrets
from types import SimpleNamespace

import pytest
from tests.native_read.test_path_access_flow import request, setup, signed
from tests.native_read.test_standing_access import standing

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.owner_native_admission import OwnerGrantProvisioning, proposed_policy
from devgraph.auth.standing_access import (
    StandingAccessDraft,
    StandingGrantSelection,
    StandingMemberPolicy,
    StandingNativePolicy,
    StandingOperatorPolicy,
)
from devgraph.auth.standing_activation import StandingAccessOperator


def fixture(tmp_path):
    s = setup(tmp_path)
    ceiling = standing(s.base[0].ceiling)
    s.base = [
        StandingMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v4",
                revision=3,
                ceiling=ceiling,
                assignments=[],
            )
        )
    ]
    raw = s.policies[0].model_dump(mode="json", by_alias=True)
    raw.update(
        schema="devgraph.access-operator-policy.v4",
        revision=4,
        ceiling=json.loads(canonical_json(ceiling)),
    )
    s.policies = [StandingOperatorPolicy.model_validate(raw)]
    s.inbox.catalog_policy = lambda: s.policies[0]
    raw = s.native[0].model_dump(mode="json", by_alias=True)
    raw.update(
        schema="secs-devgraph-work-policy.v2",
        schema_version=2,
        policy_version=raw["policy_version"] + 1,
    )
    for rule in raw["rules"]:
        # Unrelated legacy actor must retain its finite scope exactly.
        rule["actor_id"] = "pubkey:sha256:" + "0" * 64
        rule["validity"] = dict(
            schema="castalia.grant-validity.v1",
            mode="dated",
            not_before=rule.pop("not_before"),
            not_after=rule.pop("not_after"),
        )
    s.native = [StandingNativePolicy.model_validate(raw)]
    s.original = s.native[0]
    s.commits = []
    s.fail = False

    def commit(native, grant, actor, action_id):
        s.commits.append((actor, action_id))
        s.native[0] = proposed_policy(native, grant)
        if s.fail:
            raise UnauthenticatedError("native_admission_reconciliation_required")
        return s.native[0]

    s.provisioning = OwnerGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
        native_admission=SimpleNamespace(commit=commit),
    )
    s.service = StandingAccessOperator(
        inbox=s.inbox,
        policy=lambda: s.policies[0],
        storage=s.storage,
        provisioning=s.provisioning,
    )
    request(s)
    item = invoke(s, "inbox")["inbox"]["items"][0]
    raw = json.loads(canonical_json(ceiling))
    raw.update(subject_public_key=s.public, previous_digest=ceiling.digest)
    read = StandingAccessDraft.model_validate(dict(raw, installation_id="a" * 32, write_grants=[]))
    write = StandingAccessDraft.model_validate(dict(raw, read_grants=[]))
    s.binding = dict(
        request_id=item["request_id"],
        request_digest_sha256=item["request_digest_sha256"],
        expected_version=1,
        previous_assignment_digest=None,
        previous_write_assignment_digest=None,
        selection=StandingGrantSelection(read=read, write=write),
    )
    return s


def invoke(s, action, **fields):
    action = s.service.parse_action(
        dict(
            schema="devgraph.access-operator-action.v7",
            action=action,
            action_id=secrets.token_hex(16),
            **fields,
        )
    )
    c = s.service.challenge(s.key, "a" * 32, action)
    return s.service.execute(c, signed(s.operator, c))


def test_review_is_pure_and_final_exact_owner_approval_activates_pair(tmp_path):
    s = fixture(tmp_path)
    review = invoke(s, "review", **s.binding)
    assert s.commits == [] and s.native[0] == s.original
    result = invoke(s, "approve", review_id=review["review_id"], **s.binding)
    assert result["activation"]["active"] and len(s.commits) == 1
    assert s.native[0].rules[: len(s.original.rules)] == s.original.rules
    assert s.native[0].policy_version == s.original.policy_version + 1
    s.native[0].require(s.binding["selection"].write)
    assert s.provisioning.registry().assignments
    with s.inbox._database() as db:
        assert s.provisioning.write_current(db, s.public)


def test_failed_native_dispatch_cannot_commit_either_assignment(tmp_path):
    s = fixture(tmp_path)
    s.fail = True
    review = invoke(s, "review", **s.binding)
    with pytest.raises(UnauthenticatedError, match="reconciliation"):
        invoke(s, "approve", review_id=review["review_id"], **s.binding)
    with s.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM write_assignments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM access_assignments").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone() == (0,)


def test_native_head_change_requires_new_review_and_preserves_old_rules(tmp_path):
    s = fixture(tmp_path)
    review = invoke(s, "review", **s.binding)
    raw = s.native[0].model_dump(mode="json", by_alias=True)
    raw["policy_version"] += 1
    s.native[0] = StandingNativePolicy.model_validate(raw)
    with pytest.raises(UnauthenticatedError, match="exact_review_required"):
        invoke(s, "approve", review_id=review["review_id"], **s.binding)
    assert s.commits == []


def test_existing_native_deny_cannot_be_overridden_by_owner_issuance(tmp_path):
    s = fixture(tmp_path)
    raw = s.native[0].model_dump(mode="json", by_alias=True)
    rule = dict(
        raw["rules"][0],
        actor_id="pubkey:sha256:" + hashlib.sha256(bytes.fromhex(s.public)).hexdigest(),
        effect="deny",
    )
    raw["rules"].append(rule)
    raw["policy_version"] += 1
    s.native[0] = StandingNativePolicy.model_validate(raw)
    with pytest.raises(UnauthenticatedError, match="owner_native_admission_denied"):
        invoke(s, "review", **s.binding)
    assert s.commits == []


def test_refresh_does_not_hypothetically_restore_removed_native_access(tmp_path):
    s = fixture(tmp_path)
    review = invoke(s, "review", **s.binding)
    invoke(s, "approve", review_id=review["review_id"], **s.binding)
    raw = s.original.model_dump(mode="json", by_alias=True)
    raw["policy_version"] = s.native[0].policy_version + 1
    s.native[0] = StandingNativePolicy.model_validate(raw)
    s.provisioning.refresh()
    with s.inbox._database() as db:
        assert s.provisioning.write_current(db, s.public) is None


@pytest.mark.parametrize("uncertain", [False, True])
def test_local_native_cas_preserves_key_pins_and_retains_uncertain_dispatch(
    tmp_path, monkeypatch, uncertain
):
    import subprocess
    from pathlib import Path

    from devgraph.auth.owner_native_admission import OwnerNativeAdmission
    from devgraph.ops import owner_install

    (tmp_path / "request").mkdir(mode=0o700)
    s = fixture(tmp_path / "request")
    home = tmp_path / "owner"
    native = home / "Library/Application Support/Zenith/secS"
    policy_file = native / "authority/devgraph.work.v1/receiver-policy.json"
    data = home / "data"
    events = []

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(canonical_json(value))
        path.chmod(0o600)

    save(policy_file, s.native[0])
    registry = dict(
        keys=[
            dict(
                key_id="fixture",
                status="active",
                not_before=1,
                not_after=4000000000,
                public_key_base64url="fixture",
            )
        ]
    )

    def binding(p):
        return dict(
            policy_id=p.policy_id, policy_version=p.policy_version, policy_digest_sha256=p.digest
        )

    for v in ("v1", "v2"):
        bundle = data / "secrets/secs-magik" / ("devgraph.work." + v)
        save(bundle / "receiver.json", dict(policy_binding=binding(s.native[0])))
        save(bundle / "secs-public-key-registry.json", registry)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def invoke(args, **kw):
        events.append(str(args[2]))
        assert args[0] == native / "bin/secs-devgraph-work-v1"
        if args[2] == "provision":
            if uncertain:
                raise subprocess.TimeoutExpired("synthetic native", 1)
            s.native[0] = StandingNativePolicy.model_validate_json(Path(args[4]).read_bytes())
            save(policy_file, s.native[0])
        return dict(
            ready=True,
            policy_version=s.native[0].policy_version,
            policy_digest_sha256=s.native[0].digest,
            registry=registry,
        )

    monkeypatch.setattr(owner_install, "invoke", invoke)
    manager = OwnerNativeAdmission(
        data_root=data,
        state_directory=home / "journal",
        native_policy_file=policy_file,
        admin_binary=native / "bin/secs-devgraph-work-v1",
    )
    grant = s.binding["selection"].write
    if uncertain:
        with pytest.raises(UnauthenticatedError, match="reconciliation"):
            manager.commit(s.native[0], grant, s.key, "a" * 32)
        assert list((home / "journal").glob("*/started.json"))
        assert not list((home / "journal").glob("*/completed.json"))
        previous = list(events)
        with pytest.raises(UnauthenticatedError, match="reconciliation"):
            manager.commit(s.native[0], grant, s.key, "b" * 32)
        assert events == previous  # Not even a second status/provision dispatch.
    else:
        proposed = manager.commit(s.native[0], grant, s.key, "a" * 32)
        assert proposed.rules[: len(s.original.rules)] == s.original.rules
        for v in ("v1", "v2"):
            bundle = data / "secrets/secs-magik" / ("devgraph.work." + v)
            assert json.loads((bundle / "receiver.json").read_bytes())["policy_binding"] == binding(
                proposed
            )
            assert json.loads((bundle / "secs-public-key-registry.json").read_bytes()) == registry
        assert events == ["status", "provision"]


def test_owner_runtime_v9_is_explicit_and_old_configuration_stays_closed():
    from pathlib import Path

    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = dict(
        schema="devgraph.signed-read-runtime.v9",
        membership_requirement="optional",
        secs_binary_sha256="a" * 64,
    )
    for key in [
        "owner_native_admission_directory",
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
    ]:
        values[key] = "/synthetic/" + key
    assert ReadRuntimeConfiguration.model_validate(values).owner_native_admission_directory == Path(
        values["owner_native_admission_directory"]
    )
    for schema, omit in [
        ("devgraph.signed-read-runtime.v8", False),
        ("devgraph.signed-read-runtime.v9", True),
    ]:
        bad = dict(values, schema=schema)
        if omit:
            bad.pop("owner_native_admission_directory")
        with pytest.raises(ValueError):
            ReadRuntimeConfiguration.model_validate(bad)


def test_empty_arena_is_available_only_to_eligible_authenticated_operator():
    from tests.model.test_arena_runtime import add_arena

    from devgraph.arenas import ArenaMutations
    from devgraph.auth.access_catalog import scope_inventory
    from devgraph.auth.monitor_read import MonitorWorkGrant
    from devgraph.auth.path_access import PathReadGrant
    from devgraph.storage.memory import MemoryGraphStorage

    storage = MemoryGraphStorage()
    add_arena(ArenaMutations(storage), "empty")
    grant = MonitorWorkGrant(
        scope="devgraph.work.read",
        operations=["list", "get"],
        work_kinds=["Task"],
        relationship_types=["parent"],
        include_archived=False,
    )
    rows = scope_inventory(storage, [grant])
    assert [row["resource"] for row in rows] == ["Arena/empty"]
    assert rows[0]["title"] == "Empty"
    assert scope_inventory(storage, [grant], published=[]) == []
    assert scope_inventory(storage, [grant.model_copy(update={"work_ids": ("one",)})]) == []
    path = dict(schema="devgraph.resource-path.v1", path=["Arena/other"])
    scoped = PathReadGrant.model_validate(
        dict(
            grant.model_dump(mode="json", exclude_defaults=True),
            schema="devgraph.path-read-grant.v1",
            resource_paths=[path],
        )
    )
    assert scope_inventory(storage, [scoped]) == []
