import json
import secrets
import time

import pytest
from pydantic import ValidationError
from tests.native_read.test_path_access_flow import request, setup, signed

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.grant_validity import GrantValidity
from devgraph.auth.path_access import PathAccessDraft
from devgraph.auth.standing_access import (
    StandingAccessDraft,
    StandingGrantSelection,
    StandingMemberPolicy,
    StandingNativePolicy,
    StandingOperatorPolicy,
)
from devgraph.auth.standing_activation import StandingAccessOperator, StandingGrantProvisioning


def standing(old, *, mode="until_revoked", end=None):
    value = old.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    start = value.pop("not_before")
    previous_end = value.pop("not_after")
    value["schema"] = "devgraph.access-draft.v5"
    value["validity"] = {"schema": "castalia.grant-validity.v1", "mode": mode, "not_before": start}
    if mode == "dated":
        value["validity"]["not_after"] = previous_end if end is None else end
    return StandingAccessDraft.model_validate(value)


def test_new_wire_is_explicit_and_old_versions_stay_closed(tmp_path):
    s = setup(tmp_path)
    old = s.base[0].ceiling
    grant = standing(old)
    wire = json.loads(canonical_json(grant))
    assert grant.validity == GrantValidity.until_revoked(old.not_before)
    assert "not_before" not in wire and "not_after" not in wire
    assert wire["validity"]["mode"] == "until_revoked"
    assert StandingAccessDraft.model_validate_json(canonical_json(grant)) == grant
    for typ in (PathAccessDraft,):
        with pytest.raises(ValidationError):
            typ.model_validate(wire)
    for bad in (
        {**wire, "not_after": None},
        {**wire, "not_after": 9999999999},
        {**wire, "validity": None},
    ):
        with pytest.raises((ValidationError, ValueError)):
            StandingAccessDraft.model_validate(bad)


@pytest.mark.parametrize("mixed", [False, True])
def test_activation_reuse_restart_narrow_and_revoke_without_expiry(tmp_path, mixed):
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
    # Reuse retained request/decision storage. Old policies stay versioned.
    s.inbox.catalog_policy = lambda: s.policies[0]
    if mixed:
        native = s.native[0].model_dump(mode="json", by_alias=True)
        native.update(
            schema="secs-devgraph-work-policy.v2",
            schema_version=2,
            policy_version=native["policy_version"] + 1,
        )
        for rule in native["rules"]:
            start = rule.pop("not_before")
            rule.pop("not_after")
            rule["validity"] = {
                "schema": "castalia.grant-validity.v1",
                "mode": "until_revoked",
                "not_before": start,
            }
        s.native = [StandingNativePolicy.model_validate(native)]
    provisioning = StandingGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
    )
    service = StandingAccessOperator(
        inbox=s.inbox, policy=lambda: s.policies[0], storage=s.storage, provisioning=provisioning
    )
    act, _ = request(s)

    def invoke(action, **fields):
        value = dict(
            schema="devgraph.access-operator-action.v7"
            if mixed
            else "devgraph.access-operator-action.v6",
            action=action,
            action_id=secrets.token_hex(16),
            **fields,
        )
        parsed = service.parse_action(value)
        c = service.challenge(s.key, "a" * 32, parsed)
        return service.execute(c, signed(s.operator, c))

    assert (
        service.parse_action(
            dict(schema="devgraph.access-operator-action.v1", action="inbox", action_id="f" * 32)
        ).action
        == "inbox"
    )
    result = invoke("inbox")
    item = result["inbox"]["items"][0]
    assert result["activation"]["standing_validity"] is True
    raw = json.loads(canonical_json(ceiling))
    raw.update(
        subject_public_key=s.public,
        installation_id="a" * 32,
        previous_digest=ceiling.digest,
        write_grants=[],
    )
    write = None
    if mixed:
        write_raw = json.loads(canonical_json(ceiling))
        write_raw.update(
            subject_public_key=s.public, previous_digest=ceiling.digest, read_grants=[]
        )
        write = StandingAccessDraft.model_validate(write_raw)
    selection = StandingGrantSelection(read=StandingAccessDraft.model_validate(raw), write=write)
    binding = dict(
        request_id=item["request_id"],
        expected_version=item["version"],
        request_digest_sha256=item["request_digest_sha256"],
        previous_assignment_digest=item["previous_assignment_digest"],
    )
    if mixed:
        binding["previous_write_assignment_digest"] = None
    review = invoke("review", selection=selection, **binding)
    assert review["plans"]["activation"]["validity"]["mode"] == "until_revoked"
    accepted = invoke("approve", selection=selection, review_id=review["review_id"], **binding)
    assert accepted["activation"]["active"]
    assert accepted["activation"]["validity"]["mode"] == "until_revoked"
    registry = provisioning.registry()
    now = int(time.time())
    assignment = registry.assignment(s.public, "a" * 32, now + 86400 * 400)
    assert assignment.validity.credential_deadline(now, 60) == now + 60
    restarted = StandingGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
    )
    assert restarted.registry().assignment(s.public, "a" * 32, now).digest == assignment.digest
    active = invoke("inbox")["active_grants"][0]
    assert active["validity"]["mode"] == "until_revoked" and active.get("expires_at") is None
    invoke(
        "revoke",
        request_id=active["request_id"],
        expected_version=active["version"],
        request_digest_sha256=active["request_digest_sha256"],
        previous_assignment_digest=active["assignment_digest"],
        target_public_key=s.public,
        target_installation_id="a" * 32,
        reason="fixture revoke",
        **(
            {"previous_write_assignment_digest": active["write_assignment_digest"]} if mixed else {}
        ),
    )
    with pytest.raises(ValueError):
        restarted.registry().assignment(s.public, "a" * 32, now)


def test_native_finite_ceiling_cannot_admit_until_revoked_write(tmp_path):
    s = setup(tmp_path)
    grant = standing(s.base[0].ceiling)
    raw = json.loads(canonical_json(grant))
    raw.update(subject_public_key=s.public, read_grants=[])
    grant = StandingAccessDraft.model_validate(raw)
    with pytest.raises(UnauthenticatedError):
        s.native[0].require(grant)
    policy = s.native[0].model_dump(mode="json", by_alias=True)
    policy.update(schema="secs-devgraph-work-policy.v2", schema_version=2)
    for rule in policy["rules"]:
        start = rule.pop("not_before")
        rule.pop("not_after")
        rule["validity"] = {
            "schema": "castalia.grant-validity.v1",
            "mode": "until_revoked",
            "not_before": start,
        }
    native = StandingNativePolicy.model_validate(policy)
    native.require(grant)
    assert native.digest != s.native[0].digest


def test_optional_membership_never_replaces_holder_authentication(tmp_path):
    from devgraph.auth.resource_membership import ResourceMembershipAdmission

    s = setup(tmp_path)
    original = s.inbox.membership_check
    optional = ResourceMembershipAdmission(original, required=False)
    assert optional({}, s.public)
    assert not ResourceMembershipAdmission(original, required=True)({}, s.public)
    assert not optional({}, "not-a-key")
    assert not optional({"pretend": "active"}, s.public)
    s.inbox.membership_check = optional
    from tests.native_read.test_access_requests import action

    act = action(s.public)
    c = s.inbox.wallet_challenge(act)
    with pytest.raises(UnauthenticatedError):
        s.inbox.execute_wallet(c, {}, "A" * 86)
    assert s.inbox.execute_wallet(c, {}, signed(s.holder, c))["status"] == "pending"


def test_runtime_v8_requires_explicit_resource_membership_policy():
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = dict(
        schema="devgraph.signed-read-runtime.v8",
        secs_binary_sha256="a" * 64,
        **{
            k: "/private/fixture/" + k
            for k in [
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
                "member_policy_file",
                "write_native_policy_file",
                "access_inbox_database_file",
                "access_operator_policy_file",
            ]
        },
    )
    with pytest.raises(ValidationError):
        ReadRuntimeConfiguration.model_validate(values)
    assert (
        ReadRuntimeConfiguration.model_validate(
            dict(values, membership_requirement="optional")
        ).membership_requirement
        == "optional"
    )
    assert (
        ReadRuntimeConfiguration.model_validate(
            dict(values, membership_requirement="required")
        ).membership_requirement
        == "required"
    )
    with pytest.raises(ValidationError):
        ReadRuntimeConfiguration.model_validate(
            dict(
                values, schema="devgraph.signed-read-runtime.v7", membership_requirement="optional"
            )
        )


def test_real_secs_expiring_credentials_redeem_under_one_until_revoked_assignment(tmp_path):
    from datetime import timedelta

    from tests.native_read.test_http import sign
    from tests.native_read.test_member_access import members
    from tests.native_read.test_signed_authority import NOW
    from tests.native_read.test_signed_authority import request as read_request

    from devgraph.auth.monitor_read import EnrollmentPolicyV2, MemberMonitorReadEnrollment
    from devgraph.auth.resource_membership import ResourceMembershipAdmission

    old, holder, public, _, _, root, grant = members(tmp_path)
    ceiling = standing(root)
    raw = json.loads(canonical_json(ceiling))
    raw["lifetime_seconds"] = 60
    ceiling = StandingAccessDraft.model_validate(raw)
    raw = json.loads(canonical_json(standing(grant)))
    raw.update(lifetime_seconds=60, previous_digest=ceiling.digest)
    grant = StandingAccessDraft.model_validate(raw)
    registry = [
        StandingMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v4",
                revision=1,
                ceiling=ceiling,
                assignments=[grant],
            )
        )
    ]
    policy = EnrollmentPolicyV2.model_validate(
        dict(
            schema="devgraph.native-read-policy.v2",
            issuer=ceiling.issuer,
            receiver=ceiling.receiver,
            lifetime_seconds=60,
            grants=ceiling.read_grants,
        )
    )
    configured = old.authority.policy()
    configured["max_lifetime_seconds"] = 60
    old.authority.policy_file.write_text(json.dumps(configured))
    service = MemberMonitorReadEnrollment(
        path=tmp_path / "standing-real.sqlite",
        members=lambda: registry[0],
        policy=lambda: policy,
        membership_check=ResourceMembershipAdmission(old.membership_check, required=False),
        authority=old.authority,
    )

    def read(credential, now, nonce):
        req = read_request(holder, credential, nonce)
        req["proof"]["issued_at"] = int(now.timestamp())
        req["proof"].pop("signature")
        req["proof"]["signature"] = holder.sign(
            b"castalia.read-presentation.v1/signature\0" + canonical_json(req["proof"])
        ).hex()
        return service.verify_request(**req, now=now)

    first = service.challenge(public, "a" * 32, now=NOW)
    issued = service.enroll(first, {}, sign(holder, first), now=NOW)
    assert issued["credential"]["claims"]["expires_at"] == int(NOW.timestamp()) + 60
    read(issued["credential"], NOW, "81" * 16)
    later = NOW + timedelta(seconds=61)
    with pytest.raises(UnauthenticatedError):
        read(issued["credential"], later, "82" * 16)
    fresh = service.challenge(public, "a" * 32, now=later)
    renewed = service.enroll(fresh, {}, sign(holder, fresh), now=later)
    read(renewed["credential"], later, "83" * 16)
    assert registry[0].assignments[0].digest == grant.digest
    registry[0] = StandingMemberPolicy.model_validate(
        dict(schema="devgraph.member-access-policy.v4", revision=2, ceiling=ceiling, assignments=[])
    )
    with pytest.raises(UnauthenticatedError):
        read(renewed["credential"], later, "84" * 16)


def test_native_policy_v2_portable_digest():
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/standing-native-policy-v2.json").read_bytes()
    )
    policy = StandingNativePolicy.model_validate(fixture["policy"])
    assert policy.digest == fixture["digest_sha256"]


def test_runtime_v8_composes_standing_persistence_and_explicit_membership(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from devgraph.auth.path_access import compile_path_read
    from devgraph.auth.resource_membership import ResourceMembershipAdmission
    from devgraph.auth.signed_read import SecsReadAuthority
    from devgraph.ops.named_work_v2_receiver import LocalNamedWorkV2Receiver
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, load_enrollment

    fixture = tmp_path / "fixture"
    fixture.mkdir(mode=0o700)
    s = setup(fixture)
    ceiling = standing(s.base[0].ceiling)
    members = StandingMemberPolicy.model_validate(
        dict(schema="devgraph.member-access-policy.v4", revision=3, ceiling=ceiling, assignments=[])
    )
    operator = s.policies[0].model_dump(mode="json", by_alias=True)
    operator.update(
        schema="devgraph.access-operator-policy.v4",
        revision=4,
        ceiling=json.loads(canonical_json(ceiling)),
    )
    values = dict(
        enrollment_policy_file=compile_path_read(ceiling),
        member_policy_file=members,
        access_operator_policy_file=StandingOperatorPolicy.model_validate(operator),
        write_native_policy_file=s.native[0],
        membership_trust_file={},
        revoked_memberships_file=dict(schema="devgraph.revoked-memberships.v1", membership_ids=[]),
        authority_policy_file=SecsReadAuthority.policy_for(
            compile_path_read(ceiling), key_id="fixture", public_key=s.key
        ),
    )
    files = {}
    for name, value in values.items():
        target = tmp_path / (name + ".json")
        target.write_bytes(canonical_json(value))
        target.chmod(0o600)
        files[name] = target
    binary = tmp_path / "fixture-not-executed"
    binary.write_bytes(b"source composition only")
    files.update(
        secs_binary=binary,
        issuer_key_file=tmp_path / "unopened-key",
        enrollment_database_file=tmp_path / "enroll.sqlite",
        access_inbox_database_file=tmp_path / "inbox.sqlite",
    )
    seen = []
    monkeypatch.setattr(
        LocalNamedWorkV2Receiver,
        "_load_verifier",
        lambda _: SimpleNamespace(
            config=SimpleNamespace(
                policy_registry=SimpleNamespace(require_binding=lambda *v: seen.append(v))
            )
        ),
    )
    config = ReadRuntimeConfiguration.model_validate(
        dict(
            schema="devgraph.signed-read-runtime.v8",
            membership_requirement="optional",
            **files,
            secs_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        )
    )
    enrollment = load_enrollment(config, data_root=tmp_path / "receiver")
    assert isinstance(enrollment.provisioning, StandingGrantProvisioning)
    assert isinstance(enrollment.membership_check, ResourceMembershipAdmission)
    assert enrollment.membership_check({}, s.public)
    assert enrollment.provisioning.registry().ceiling.validity.not_after is None
    # Exercise the composed HTTP path, not a directly constructed policy: later
    # provisioning imports must not change the inbox's selected v8 decoder.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from devgraph.api.access_requests import register_access_request_routes

    inbox = enrollment.provisioning.inbox
    inbox.storage = s.storage
    app = FastAPI()
    register_access_request_routes(app, inbox)
    with TestClient(app, base_url=inbox.receiver) as client:
        response = client.get("/devgraph/access-requests/v2/catalog")
    assert response.status_code == 200
    available = response.json()
    assert {p["id"] for p in available["presets"]} == {"contributor", "project-manager"}
    assert available["scopes"]
    assert all(row["resource_path"]["path"][0] == "Arena/a" for row in available["scopes"])
    assert isinstance(inbox.catalog_policy(), StandingOperatorPolicy)
    assert seen and all(
        v == (s.native[0].policy_id, s.native[0].policy_version, s.native[0].digest) for v in seen
    )
