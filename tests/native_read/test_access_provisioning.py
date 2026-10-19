"""Disposable operator-to-assignment tests; never install authority or touch live data."""

import json
import secrets
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_access_operator import call, fixture, selection, signed

from devgraph.auth.access_operator import AccessOperator, OperatorAction
from devgraph.auth.access_provisioning import ReadGrantProvisioning
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import MemberAccessPolicyV2


def setup(tmp_path):
    inbox, _, holder, public, proof, op, key, policy = fixture(tmp_path)
    base = [
        MemberAccessPolicyV2.model_validate(
            dict(
                schema="devgraph.member-access-policy.v2",
                revision=1,
                ceiling=policy[0].ceiling,
                assignments=[],
            )
        )
    ]
    provisioning = ReadGrantProvisioning(inbox=inbox, members=lambda: base[0])
    service = AccessOperator(inbox=inbox, policy=lambda: policy[0], provisioning=provisioning)
    return inbox, service, holder, public, proof, op, key, policy, base, provisioning


def fields(service, op, key, policy, public):
    item = call(service, op, key, "inbox")["inbox"]["items"][0]
    chosen = selection(policy[0], public)
    chosen.pop("write")
    return dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
        selection=chosen,
        previous_assignment_digest=item["previous_assignment_digest"],
    )


def v2(service, key, public, kind, **fields):
    action = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v2",
            action=kind,
            action_id=secrets.token_hex(16),
            **fields,
        )
    )
    challenge = service.challenge(public, "a" * 32, action)
    return service.execute(challenge, signed(key, challenge))


def approve(service, op, key, params):
    reviewed = v2(service, op, key, "review", **params)
    return v2(service, op, key, "approve", review_id=reviewed["review_id"], **params)


def test_review_approve_activates_once_and_status_never_repeats_it(tmp_path):
    inbox, service, _, public, _, op, key, policy, _, provisioning = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    assert provisioning.registry().assignments == ()
    result = approve(service, op, key, params)
    assert result["status"] == "active" and result["activation"]["active"]
    assert (
        result["authority_issued"] is False
    )  # Approval activates a grant; holder enrolls separately.
    registry = provisioning.registry()
    assert (
        registry.assignment(public, "a" * 32, int(datetime.now(timezone.utc).timestamp())).digest
        == result["activation"]["assignment_digest"]
    )
    status = call(service, op, key, "status", original_action_id=result["action_id"])
    assert status["result"] == result and provisioning.registry().revision == registry.revision
    with inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_provisioning_audit").fetchone()[0] == 1
    with pytest.raises(UnauthenticatedError, match="request_changed"):
        approve(service, op, key, params)


def test_activation_failure_rolls_back_decision_assignment_and_receipt(tmp_path, monkeypatch):
    inbox, service, _, public, _, op, key, policy, _, provisioning = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    review = v2(service, op, key, "review", **params)
    actual = provisioning.activate

    def fail(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("simulated interruption after activation")

    monkeypatch.setattr(provisioning, "activate", fail)
    with pytest.raises(RuntimeError):
        v2(service, op, key, "approve", review_id=review["review_id"], **params)
    assert provisioning.registry().assignments == ()
    with inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM access_provisioning_audit").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT count(*) FROM access_operator_audit WHERE operation='approve'"
            ).fetchone()[0]
            == 0
        )


def test_writes_fail_before_consent_and_legacy_actions_do_not_activate(tmp_path):
    inbox, service, _, public, _, op, key, policy, _, provisioning = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    with pytest.raises(UnauthenticatedError, match="activation_upgrade_required"):
        call(
            service,
            op,
            key,
            "review",
            **{k: v for k, v in params.items() if k != "previous_assignment_digest"},
        )
    params["selection"] = selection(policy[0], public)
    with pytest.raises(UnauthenticatedError, match="write_activation_unavailable"):
        v2(service, op, key, "review", **params)
    assert provisioning.registry().assignments == ()
    with pytest.raises(ValueError):
        OperatorAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v1",
                action="inbox",
                action_id="f" * 32,
                previous_assignment_digest=None,
            )
        )


def test_exact_replacement_and_revoke_do_not_resurrect_a_base_assignment(tmp_path):
    inbox, service, _, public, _, op, key, policy, base, provisioning = setup(tmp_path)
    grant = selection(policy[0], public)["read"]
    base[0] = base[0].model_copy(
        update={"revision": 2, "assignments": (base[0].ceiling.model_validate(grant),)}
    )
    params = fields(service, op, key, policy, public)
    assert params["previous_assignment_digest"]
    with pytest.raises(UnauthenticatedError, match="assignment_changed"):
        approve(service, op, key, {**params, "previous_assignment_digest": None})
    result = approve(service, op, key, params)
    revoked = v2(
        service,
        op,
        key,
        "revoke",
        target_public_key=public,
        target_installation_id="a" * 32,
        request_id=params["request_id"],
        request_digest_sha256=params["request_digest_sha256"],
        expected_version=2,
        previous_assignment_digest=result["activation"]["assignment_digest"],
        reason="Member left the project",
    )
    assert revoked["status"] == "revoked" and not revoked["activation"]["active"]
    assert provisioning.registry().assignments == ()
    assert (
        call(service, op, key, "status", original_action_id=revoked["action_id"])["result"]
        == revoked
    )
    assert ReadGrantProvisioning(inbox=inbox, members=lambda: base[0]).registry().assignments == ()


def test_observed_base_narrowing_and_rollback_never_revive_old_grants(tmp_path):
    inbox, service, _, public, _, op, key, policy, base, provisioning = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    approve(service, op, key, params)
    original = base[0]
    grant = original.ceiling.read_grants[0].model_copy(update={"work_ids": ("Task/one",)})
    base[0] = original.model_copy(
        update={
            "revision": 2,
            "ceiling": original.ceiling.model_copy(update={"read_grants": (grant,)}),
        }
    )
    assert provisioning.registry().assignments == ()
    base[0] = original.model_copy(update={"revision": 3})
    assert provisioning.registry().assignments == ()
    base[0] = original
    with pytest.raises(UnauthenticatedError, match="rollback"):
        provisioning.registry()


@pytest.mark.parametrize("operator_kind", ["native", "wasm"])
def test_read_credential_reuse_native_secs_then_operator_revocation(
    tmp_path, monkeypatch, operator_kind
):
    from tests.native_read.test_access_requests import action
    from tests.native_read.test_http import sign
    from tests.native_read.test_signed_authority import NOW, request
    from tests.native_read.test_signed_authority import setup as authority_setup

    from devgraph.auth.access_operator import OperatorPolicy
    from devgraph.auth.access_provisioning import ProvisionedMonitorReadEnrollment
    from devgraph.auth.access_requests import AccessRequestInbox
    from devgraph.auth.monitor_read import AccessDraftV2, EnrollmentPolicyV2
    from devgraph.auth.signed_read import SecsReadAuthority

    old, owner, _, policies, _ = authority_setup(tmp_path)
    public = owner.public_key().public_bytes_raw().hex()
    with old._database() as db:
        proof = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
    seconds = int(NOW.timestamp())
    for module in ("access_operator", "access_requests", "native_read", "signed_read"):
        monkeypatch.setattr("devgraph.auth." + module + "._seconds", lambda _: seconds)
    root = AccessDraftV2.model_validate(
        dict(
            schema="devgraph.access-draft.v2",
            issuer=policies[0].issuer,
            receiver=policies[0].receiver,
            write_audience="devgraph://receiver-local",
            not_before=seconds - 1,
            not_after=seconds + 3600,
            lifetime_seconds=600,
            read_grants=[
                dict(
                    scope="devgraph.work.read",
                    operations=["list", "get"],
                    relationship_types=["parent"],
                    work_kinds=["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"],
                    include_archived=False,
                )
            ],
        )
    )
    base = MemberAccessPolicyV2.model_validate(
        dict(schema="devgraph.member-access-policy.v2", revision=1, ceiling=root, assignments=[])
    )
    policy = EnrollmentPolicyV2.model_validate(
        dict(
            schema="devgraph.native-read-policy.v2",
            issuer=root.issuer,
            receiver=root.receiver,
            lifetime_seconds=600,
            grants=root.read_grants,
        )
    )
    pins = old.authority.policy()
    old.authority.policy_file.write_text(
        json.dumps(
            SecsReadAuthority.policy_for(
                policy, key_id=pins["key_id"], public_key=pins["public_key"]
            )
        )
    )
    inbox = AccessRequestInbox(
        path=tmp_path / "provision.sqlite",
        issuer=root.issuer,
        receiver=root.receiver,
        membership_check=old.membership_check,
    )
    challenge = inbox.wallet_challenge(action(public))
    inbox.execute_wallet(challenge, proof, signed(owner, challenge))
    provisioning = ReadGrantProvisioning(inbox=inbox, members=lambda: base)
    operator = Ed25519PrivateKey.generate()
    key = operator.public_key().public_bytes_raw().hex()
    if operator_kind == "wasm":
        import os
        import subprocess

        driver = os.environ.get("CASTALIA_ACCESS_REQUEST_FIXTURE_DRIVER")
        if not driver:
            pytest.skip("matched Wallet WASM driver required")

        def core(**body):
            output = subprocess.run(
                [os.environ.get("CASTALIA_TEST_NODE", "node"), driver],
                input=json.dumps(body),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return json.loads(output.stdout)

        key = core(operation="identity")["subject_public_key"]
    op_policy = OperatorPolicy.model_validate(
        dict(
            schema="devgraph.access-operator-policy.v1",
            revision=1,
            operator_public_keys=[key],
            ceiling=root,
        )
    )
    service = AccessOperator(inbox=inbox, policy=lambda: op_policy, provisioning=provisioning)
    enrollment = ProvisionedMonitorReadEnrollment(
        path=tmp_path / "provisioned-enroll.sqlite",
        policy=lambda: policy,
        authority=old.authority,
        membership_check=old.membership_check,
        provisioning=provisioning,
    )
    with pytest.raises(UnauthenticatedError):
        enrollment.challenge(public, "a" * 32, now=NOW)

    def operator_call(kind, **params):
        action_ = OperatorAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v1"
                if kind in ("inbox", "status")
                else "devgraph.access-operator-action.v2",
                action=kind,
                action_id=secrets.token_hex(16),
                **params,
            )
        )
        challenge_ = service.challenge(key, "b" * 32, action_)
        signature_ = (
            core(operation="approve", challenge=challenge_, now=seconds)["signature"]
            if operator_kind == "wasm"
            else signed(operator, challenge_)
        )
        return service.execute(challenge_, signature_)

    item = operator_call("inbox")["inbox"]["items"][0]
    params = dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
        selection={"read": selection(op_policy, public)["read"]},
        previous_assignment_digest=item["previous_assignment_digest"],
    )
    params["selection"]["read"]["read_grants"][0]["work_ids"] = ["Task/allowed-1"]
    reviewed = operator_call("review", **params)
    activated = operator_call("approve", review_id=reviewed["review_id"], **params)
    challenge = enrollment.challenge(public, "a" * 32, now=NOW)
    result = enrollment.enroll(challenge, proof, sign(owner, challenge), now=NOW)
    credential = result["credential"]
    assert result["schema"] == "devgraph.native-read-result.v3"
    assert credential["claims"]["holder_public_key"] == public
    for nonce in ("11" * 16, "22" * 16):
        context = enrollment.verify_request(**request(owner, credential, nonce), now=NOW)
        assert context.read_grants[0].work_ids == ("Task/allowed-1",)
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    started = Event()

    def revoke_during_read():
        started.set()
        return operator_call(
            "revoke",
            target_public_key=public,
            target_installation_id="a" * 32,
            request_id=params["request_id"],
            request_digest_sha256=params["request_digest_sha256"],
            expected_version=2,
            previous_assignment_digest=activated["activation"]["assignment_digest"],
            reason="End the disposable test",
        )

    # The assignment database stays read-locked inside credential authorization.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with enrollment._using_registry():
            with enrollment._database() as db:
                db.execute("BEGIN IMMEDIATE")
                enrollment._check_policy_snapshot(db)
                future = pool.submit(revoke_during_read)
                assert started.wait(timeout=2)
                from concurrent.futures import TimeoutError

                with pytest.raises(TimeoutError):
                    future.result(timeout=0.1)
                db.execute("COMMIT")
        assert future.result(timeout=10)["status"] == "revoked"
    with pytest.raises(UnauthenticatedError):
        enrollment.verify_request(**request(owner, credential, "33" * 16), now=NOW)
    with pytest.raises(UnauthenticatedError):
        enrollment.challenge(public, "a" * 32, now=NOW)


def test_requester_status_distinguishes_active_expired_and_revoked_grants(tmp_path):
    from datetime import timedelta

    from tests.native_read.test_access_requests import action

    inbox, service, holder, public, proof, op, key, policy, _, _ = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    result = approve(service, op, key, params)

    def status(now=None):
        challenge = inbox.wallet_challenge(action(public, action="status"), now=now)
        return inbox.execute_wallet(challenge, proof, signed(holder, challenge), now=now)

    active = status()
    assert active["schema"] == "devgraph.access-request-result.v3"
    assert active["status"] == "active" and active["grant_active"]
    assert active["authority_issued"] is False
    expired = status(datetime.now(timezone.utc) + timedelta(hours=2))
    assert expired["status"] == "expired" and not expired["grant_active"]
    v2(
        service,
        op,
        key,
        "revoke",
        target_public_key=public,
        target_installation_id="a" * 32,
        request_id=params["request_id"],
        request_digest_sha256=params["request_digest_sha256"],
        expected_version=2,
        previous_assignment_digest=result["activation"]["assignment_digest"],
        reason="End the grant",
    )
    assert status()["status"] == "revoked"


def test_issuer_refuses_assignment_widening_despite_a_broader_operator_ceiling(tmp_path):
    inbox, service, _, public, _, op, key, policy, base, _ = setup(tmp_path)
    root = base[0].ceiling
    narrow = root.read_grants[0].model_copy(update={"work_ids": ("Task/one",)})
    base[0] = base[0].model_copy(
        update={"revision": 2, "ceiling": root.model_copy(update={"read_grants": (narrow,)})}
    )
    params = fields(service, op, key, policy, public)
    with pytest.raises(ValueError, match="amplification"):
        approve(service, op, key, params)
    with inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_assignments").fetchone()[0] == 0


def test_published_activation_schema_and_actual_wasm_consent_vector():
    import hashlib
    from pathlib import Path

    from devgraph.auth.access_approval import DOMAIN, verify_wallet_approval
    from devgraph.auth.delegated_contract import canonical_json

    root = Path(__file__).parents[2] / "docs/contracts/castalia-access-request/v2"
    assert json.loads(
        (root.parent / "v3/operator-action.schema.json").read_text()
    ) == OperatorAction.model_json_schema(by_alias=True)
    assert (
        hashlib.sha256((root / "operator-action.schema.json").read_bytes()).hexdigest()
        == "654ad367068001d68bff387a01c1b6a00c5878579f055dc220fb2b8667cf8332"
    )
    vector = json.loads((root / "review-approval-vector.json").read_text())
    challenge = vector["challenge"]
    assert (
        hashlib.sha256(DOMAIN + canonical_json(challenge)).hexdigest() == vector["signing_sha256"]
    )
    assert (
        verify_wallet_approval(
            challenge,
            vector["signature"],
            issuer="fixture",
            receiver="https://example.test",
            current=100,
        ).operation
        == "access.review"
    )
    parsed = OperatorAction.model_validate(challenge["details"]["action"])
    assert parsed.wire()["previous_assignment_digest"] is None


def test_saved_legacy_approval_cannot_gain_activation_after_runtime_upgrade(tmp_path):
    inbox, old, _, public, _, op, key, policy = fixture(tmp_path)
    item = call(old, op, key, "inbox")["inbox"]["items"][0]
    chosen = selection(policy[0], public)
    chosen.pop("write")
    params = dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
        selection=chosen,
    )
    review = call(old, op, key, "review", **params)
    action = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v1",
            action="approve",
            action_id="e" * 32,
            review_id=review["review_id"],
            **params,
        )
    )
    challenge = old.challenge(key, "a" * 32, action)
    signature = signed(op, challenge)
    base = MemberAccessPolicyV2.model_validate(
        dict(
            schema="devgraph.member-access-policy.v2",
            revision=9,
            ceiling=policy[0].ceiling,
            assignments=[],
        )
    )
    provisioning = ReadGrantProvisioning(inbox=inbox, members=lambda: base)
    new = AccessOperator(inbox=inbox, policy=lambda: policy[0], provisioning=provisioning)
    with pytest.raises(UnauthenticatedError, match="activation_upgrade_required"):
        new.execute(challenge, signature)
    assert provisioning.registry().assignments == ()
    assert provisioning.registry().revision == 9


def test_replacement_invalidates_old_request_and_old_revocation_cannot_remove_new_grant(tmp_path):
    from tests.native_read.test_access_requests import action

    inbox, service, holder, public, proof, op, key, policy, _, provisioning = setup(tmp_path)
    first = fields(service, op, key, policy, public)
    old = approve(service, op, key, first)
    challenge = inbox.wallet_challenge(action(public, identifier="f" * 32))
    inbox.execute_wallet(challenge, proof, signed(holder, challenge))
    second = fields(service, op, key, policy, public)
    assert second["previous_assignment_digest"] == old["activation"]["assignment_digest"]
    second["selection"]["read"]["read_grants"][0]["work_ids"] = ["Task/one"]
    new = approve(service, op, key, second)
    old_status = inbox.wallet_challenge(action(public, action="status"))
    assert (
        inbox.execute_wallet(old_status, proof, signed(holder, old_status))["status"]
        == "superseded"
    )
    with pytest.raises(UnauthenticatedError, match="assignment_changed"):
        v2(
            service,
            op,
            key,
            "revoke",
            target_public_key=public,
            target_installation_id="a" * 32,
            request_id=first["request_id"],
            request_digest_sha256=first["request_digest_sha256"],
            expected_version=2,
            previous_assignment_digest=old["activation"]["assignment_digest"],
            reason="Stale revoke",
        )
    assert provisioning.registry().assignments[0].digest == new["activation"]["assignment_digest"]
