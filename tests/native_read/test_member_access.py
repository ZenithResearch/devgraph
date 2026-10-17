"""Member-specific issuer selections are enforced by the real signed read path."""

import json

import pytest
from tests.auth.test_access_policy import draft, narrow
from tests.native_read.test_http import sign
from tests.native_read.test_signed_authority import NOW, request, setup

from devgraph.auth.access_policy import MemberAccessPolicy
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.member_read import MemberReadEnrollment


def members(tmp_path):
    old, owner, _, policies, _ = setup(tmp_path)
    public = owner.public_key().public_bytes_raw().hex()
    root = draft(
        issuer=policies[0].issuer,
        receiver=policies[0].receiver,
        not_before=int(NOW.timestamp()) - 1,
        not_after=int(NOW.timestamp()) + 7200,
        read_grants=policies[0].grants,
        write_grants=[],
    )
    grant = narrow(
        root,
        subject_public_key=public,
        installation_id="a" * 32,
        not_after=int(NOW.timestamp()) + 300,
    )
    registry = [
        MemberAccessPolicy.model_validate(
            {
                "schema": "devgraph.member-access-policy.v1",
                "ceiling": root,
                "assignments": [grant],
            }
        )
    ]
    service = MemberReadEnrollment(
        path=tmp_path / "members.sqlite",
        policy=old.policy,
        membership_check=old.membership_check,
        authority=old.authority,
        members=lambda: registry[0],
    )
    with old._database() as db:
        proof = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
    return service, owner, public, proof, registry, root, grant


def test_member_policy_selection_expiry_and_holder_signature(tmp_path):
    service, owner, public, proof, _, _, _ = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    assert challenge["credential_expires_at"] == int(NOW.timestamp()) + 300
    with pytest.raises(UnauthenticatedError):
        service.challenge(public, "b" * 32, now=NOW)
    result = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)
    context = service.verify_request(**request(owner, result["credential"]), now=NOW)
    assert context.read_grants[0].work_ids == ("Task/allowed-1", "Task/allowed-2")
    altered = json.loads(canonical_json(challenge))
    altered["details"]["read_grants"][0]["work_ids"].append("Task/hidden")
    with pytest.raises(UnauthenticatedError):
        service.enroll(altered, proof, sign(owner, altered), now=NOW)


def test_narrowing_or_removal_invalidates_outstanding_challenges_and_credentials(tmp_path):
    service, owner, public, proof, registry, root, grant = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    result = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)
    pending = service.challenge(public, "a" * 32, now=NOW)
    reads = json.loads(canonical_json(grant))["read_grants"]
    reads[0]["work_ids"] = ["Task/allowed-1"]
    narrower = narrow(grant, read_grants=reads)
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": registry[0].revision + 1,
            "ceiling": root,
            "assignments": [narrower],
        }
    )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, result["credential"]), now=NOW)
    with pytest.raises(UnauthenticatedError):
        service.enroll(pending, proof, sign(owner, pending), now=NOW)
    fresh = service.challenge(public, "a" * 32, now=NOW)
    limited = service.enroll(fresh, proof, sign(owner, fresh), now=NOW)
    assert service.verify_request(**request(owner, limited["credential"]), now=NOW).read_grants[
        0
    ].work_ids == ("Task/allowed-1",)
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": registry[0].revision + 1,
            "ceiling": root,
            "assignments": [],
        }
    )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, limited["credential"], "22" * 16), now=NOW)


def test_registry_rejects_duplicate_members_and_amplification():
    root = draft()
    grant = narrow(root, subject_public_key="a" * 64, installation_id="b" * 32)
    for grants in (
        [grant, grant],
        [draft(subject_public_key="a" * 64, installation_id="b" * 32, not_after=20000)],
    ):
        with pytest.raises(ValueError):
            MemberAccessPolicy.model_validate(
                {
                    "schema": "devgraph.member-access-policy.v1",
                    "ceiling": root,
                    "assignments": grants,
                }
            )


def test_two_real_members_are_isolated_before_pagination(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.api.test_app_scaffold import build_services
    from tests.native_read.test_http import fixture

    from devgraph.api.signed_read import create_signed_read_app
    from devgraph.auth.enforcement import AuditLog
    from devgraph.model.repository import WorkObjectRepository
    from devgraph.model.work import Task

    service, owner, public, proof, registry, root, grant = members(tmp_path)
    second_dir = tmp_path / "second"
    second_dir.mkdir(mode=0o700)
    other, other_public, other_proof, _, _, other_service = fixture(second_dir)
    grants = json.loads(canonical_json(grant))["read_grants"]
    grants[0]["work_ids"] = ["Task/allowed-1"]
    first = narrow(grant, read_grants=grants)
    grants[0]["work_ids"] = ["Task/allowed-2"]
    second = narrow(
        root, read_grants=grants, subject_public_key=other_public, installation_id="b" * 32
    )
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": registry[0].revision + 1,
            "ceiling": root,
            "assignments": [first, second],
        }
    )
    original_check = service.membership_check
    service.membership_check = lambda p, key: (
        original_check(p, key) or other_service.membership_check(p, key)
    )
    storage = build_services(frozenset()).storage
    repo = WorkObjectRepository(storage)
    for identifier in ("allowed-1", "allowed-2", "hidden"):
        repo.create(Task(id=identifier, title=identifier))
    monkeypatch.setattr("devgraph.auth.signed_read._seconds", lambda _: int(NOW.timestamp()))
    http = TestClient(
        create_signed_read_app(storage=storage, enrollment=service, audit_log=AuditLog())
    )
    for key, member, membership, device, expected in (
        (owner, public, proof, "a" * 32, "allowed-1"),
        (other, other_public, other_proof, "b" * 32, "allowed-2"),
    ):
        challenge = service.challenge(member, device, now=NOW)
        credential = service.enroll(challenge, membership, sign(key, challenge), now=NOW)[
            "credential"
        ]
        result = http.post("/devgraph/native-read/v2/requests", json=request(key, credential))
        assert result.status_code == 200
        assert [item["id"] for item in result.json()["items"]] == [expected]
        impersonated = request(other if key is owner else owner, credential, "22" * 16)
        assert http.post("/devgraph/native-read/v2/requests", json=impersonated).status_code == 401
    # A caller cannot submit desired grants or install a policy through enrollment.
    assert (
        http.post(
            "/devgraph/native-read/v2/challenges",
            json={
                "subject_public_key": public,
                "installation_id": "a" * 32,
                "grants": [],
            },
        ).status_code
        == 401
    )


def test_changed_deadline_invalidates_existing_credential_and_old_challenge(tmp_path):
    service, owner, public, proof, registry, root, grant = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    credential = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)["credential"]
    pending = service.challenge(public, "a" * 32, now=NOW)
    shorter = narrow(grant, not_after=int(NOW.timestamp()) + 120)
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": registry[0].revision + 1,
            "ceiling": root,
            "assignments": [shorter],
        }
    )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW)
    with pytest.raises(UnauthenticatedError):
        service.enroll(pending, proof, sign(owner, pending), now=NOW)


def test_registry_rollback_and_readded_assignment_do_not_revive_old_authority(tmp_path):
    service, owner, public, proof, registry, root, grant = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    credential = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)["credential"]
    original = registry[0]
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": 2,
            "ceiling": root,
            "assignments": [],
        }
    )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW)
    registry[0] = original
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential, "22" * 16), now=NOW)
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": 3,
            "ceiling": root,
            "assignments": [grant],
        }
    )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential, "33" * 16), now=NOW)
    # A new explicitly approved enrollment is still possible after regranting.
    fresh = service.challenge(public, "a" * 32, now=NOW)
    replacement = service.enroll(fresh, proof, sign(owner, fresh), now=NOW)["credential"]
    service.verify_request(**request(owner, replacement, "44" * 16), now=NOW)


def test_other_member_changes_do_not_require_everyone_to_reenroll(tmp_path):
    service, owner, public, proof, registry, root, grant = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    credential = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)["credential"]
    another = narrow(root, subject_public_key="a" * 64, installation_id="c" * 32)
    registry[0] = MemberAccessPolicy.model_validate(
        {
            "schema": "devgraph.member-access-policy.v1",
            "revision": 2,
            "ceiling": root,
            "assignments": [grant, another],
        }
    )
    service.verify_request(**request(owner, credential), now=NOW)


def test_concurrent_revocation_invalidates_snapshot_and_survives_restart(tmp_path):
    from devgraph.auth.signed_read import SignedReadEnrollment

    service, owner, public, proof, registry, root, _ = members(tmp_path)
    challenge = service.challenge(public, "a" * 32, now=NOW)
    credential = service.enroll(challenge, proof, sign(owner, challenge), now=NOW)["credential"]
    original = registry[0]
    with service._using_registry():
        registry[0] = MemberAccessPolicy.model_validate(
            {
                "schema": "devgraph.member-access-policy.v1",
                "revision": 2,
                "ceiling": root,
                "assignments": [],
            }
        )
        # Another receiver process observes and commits the revocation first.
        MemberReadEnrollment(
            path=service.path,
            policy=service.policy,
            membership_check=service.membership_check,
            authority=service.authority,
            members=lambda: registry[0],
        )
        with pytest.raises(UnauthenticatedError, match="policy_changed"):
            SignedReadEnrollment.verify_request(service, **request(owner, credential), now=NOW)
    registry[0] = original
    with pytest.raises(UnauthenticatedError, match="rollback"):
        MemberReadEnrollment(
            path=service.path,
            policy=service.policy,
            membership_check=service.membership_check,
            authority=service.authority,
            members=lambda: registry[0],
        )
