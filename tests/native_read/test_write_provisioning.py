"""Durable activation -> existing native projection -> guarded receiver. Disposable data only."""

import hashlib
import secrets
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.auth.test_secs_work import KEY, config
from tests.auth.test_secs_work_v2 import proof_v2
from tests.auth.test_write_scope import command, create
from tests.native_read.test_access_operator import call, fixture, signed
from tests.native_read.test_access_requests import action

from devgraph.auth import AuditLog
from devgraph.auth.access_operator import AccessOperator, OperatorAction, OperatorPolicyV2
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import MemberAccessPolicyV2
from devgraph.auth.secs_issue_create import (
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
)
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.auth.secs_work_v2 import SecSWorkV2Verifier
from devgraph.auth.write_provisioning import (
    NativeWritePolicy,
    ProvisionedWriteReceiver,
    WriteAccessDraft,
    WriteGrantProvisioning,
)
from devgraph.storage.memory import MemoryGraphStorage


def setup(tmp_path):
    inbox, _, holder, public, proof, operator, key, old_policy = fixture(tmp_path)
    raw = old_policy[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    raw["write_grants"] = [dict(operation="devgraph.work.patch.v2", resources=[dict(kind="Task")])]
    base = [
        MemberAccessPolicyV2.model_validate(
            dict(schema="devgraph.member-access-policy.v2", revision=1, ceiling=raw, assignments=[])
        )
    ]
    raw["schema"] = "devgraph.access-draft.v3"
    policies = [
        OperatorPolicyV2.model_validate(
            dict(
                schema="devgraph.access-operator-policy.v2",
                revision=2,
                operator_public_keys=[key],
                ceiling=raw,
            )
        )
    ]
    actor = "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()
    native = [
        NativeWritePolicy.model_validate(
            dict(
                schema="secs-devgraph-work-policy.v1",
                schema_version=1,
                audience="devgraph://receiver-local",
                policy_id="test-policy",
                policy_version=1,
                rules=[
                    dict(
                        actor_id=actor,
                        operation="devgraph.work.patch.v2",
                        resource="Task/",
                        resource_match="prefix",
                        effect="allow",
                        status="active",
                        not_before=raw["not_before"],
                        not_after=raw["not_after"],
                    )
                ],
            )
        )
    ]
    provisioning = WriteGrantProvisioning(
        inbox=inbox,
        members=lambda: base[0],
        operators=lambda: policies[0],
        native=lambda: native[0],
    )
    service = AccessOperator(inbox=inbox, policy=lambda: policies[0], provisioning=provisioning)
    return SimpleNamespace(**locals())


def fields(s):
    item = call(s.service, s.operator, s.key, "inbox")["inbox"]["items"][0]
    value = s.policies[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    value.pop("read_grants", None)
    value.update(subject_public_key=s.public, previous_digest=s.policies[0].ceiling.digest)
    value["write_grants"][0]["resources"][0]["ids"] = ["child"]
    return dict(
        request_id=item["request_id"],
        request_digest_sha256=item["request_digest_sha256"],
        expected_version=1,
        selection={"write": value},
        previous_assignment_digest=item["previous_write_assignment_digest"],
    )


def invoke(s, kind, **values):
    act = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v3",
            action=kind,
            action_id=secrets.token_hex(16),
            **values,
        )
    )
    challenge = s.service.challenge(s.key, "a" * 32, act)
    return s.service.execute(challenge, signed(s.operator, challenge))


def approve(s):
    params = fields(s)
    review = invoke(s, "review", **params)
    return invoke(s, "approve", review_id=review["review_id"], **params)


def receiver(s):
    storage = MemoryGraphStorage()
    audit = AuditLog()
    create(storage, "Task", "child")

    def verifier():
        return SecSWorkV2Verifier(
            replace(
                config(),
                clock=lambda: int(time.time()),
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [
                        SecSIssueCreatePolicyBinding(
                            policy_id=s.native[0].policy_id,
                            policy_version=s.native[0].policy_version,
                            policy_digest_sha256=s.native[0].digest,
                        )
                    ]
                ),
            )
        )

    core = SimpleNamespace(storage=storage, audit_log=audit, _load_verifier=verifier)
    return ProvisionedWriteReceiver(receiver=core, provisioning=s.provisioning), storage, audit


def execute(s, received, kind="execute"):
    raw = command().canonical
    now = int(time.time())
    projection = proof_v2(
        raw,
        actor_id=s.actor,
        receiver_policy_digest_sha256=s.native[0].digest,
        issued_at=now,
        expires_at=now + 60,
    )
    return getattr(received, kind)(
        request_json=raw, projection_json=projection, idempotency_key=KEY
    )


def test_approval_persists_and_admits_native_authority_without_duplicate_mutation(tmp_path):
    s = setup(tmp_path)
    result = approve(s)
    assert result["status"] == "active" and result["activation"]["holder_wide"]
    assert result["authority_issued"] is False and not result["activation"]["credential_issued"]
    s.provisioning = WriteGrantProvisioning(
        inbox=s.inbox,
        members=lambda: s.base[0],
        operators=lambda: s.policies[0],
        native=lambda: s.native[0],
    )
    received, storage, audit = receiver(s)
    work, receipt, duplicate = execute(s, received)
    assert work.version == 2 and not duplicate
    assert execute(s, received)[1].id == receipt.id
    assert execute(s, received, "status").id == receipt.id
    assert len(storage.query("EventReceipt")) == 1 and len(audit.records) == 3
    assert s.provisioning.registry().assignments == ()


def revoke(s, result):
    return invoke(
        s,
        "revoke",
        request_id=result["request_id"],
        expected_version=2,
        request_digest_sha256=result["request_digest_sha256"],
        previous_assignment_digest=result["activation"]["assignment_digest"],
        target_public_key=s.public,
        target_installation_id="a" * 32,
        reason="Test finished",
    )


def test_revoke_survives_restart_and_denies_current_and_future_requests(tmp_path):
    s = setup(tmp_path)
    result = approve(s)
    received, storage, _ = receiver(s)
    execute(s, received)
    assert revoke(s, result)["activation"]["active"] is False
    for kind in ["execute", "status"]:
        with pytest.raises(SecSWorkDenied):
            execute(s, received, kind)
    with s.provisioning.admission(stable_issuer="secs:test-work") as policy:
        assert policy.assignments == ()
    assert len(storage.query("EventReceipt")) == 1


def test_activation_interrupt_is_atomic_and_original_status_remains_unknown(tmp_path, monkeypatch):
    s = setup(tmp_path)
    params = fields(s)
    review = invoke(s, "review", **params)
    original = s.provisioning.activate

    def fail(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(s.provisioning, "activate", fail)
    with pytest.raises(RuntimeError):
        invoke(s, "approve", review_id=review["review_id"], **params)
    with s.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM write_assignments").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone()[0] == 0


def test_native_ceiling_is_required_before_wallet_consent_and_cannot_be_expanded(tmp_path):
    s = setup(tmp_path)
    p = fields(s)
    s.native[0] = s.native[0].model_copy(
        update={
            "policy_version": 2,
            "rules": (
                s.native[0]
                .rules[0]
                .model_copy(update={"resource": "Task/other", "resource_match": "exact"}),
            ),
        }
    )
    with pytest.raises(UnauthenticatedError, match="native_admission_required"):
        invoke(s, "review", **p)
    with s.inbox._database() as db:
        assert (
            db.execute("SELECT count(*) FROM access_operator_challenges WHERE used=0").fetchone()[0]
            == 0
        )


def test_narrowing_and_policy_rollback_never_resurrect_write_assignments(tmp_path):
    s = setup(tmp_path)
    approve(s)
    original = s.native[0]
    s.native[0] = original.model_copy(
        update={
            "policy_version": 2,
            "rules": (original.rules[0].model_copy(update={"effect": "deny"}),),
        }
    )
    s.provisioning.refresh()
    s.native[0] = original.model_copy(update={"policy_version": 3})
    with s.provisioning.admission(stable_issuer="secs:test-work") as policy:
        assert policy.assignments == ()
    s.native[0] = original
    with pytest.raises(UnauthenticatedError, match="write_policy_rollback"):
        s.provisioning.refresh()


def test_pending_projection_cannot_escape_current_receiver_assignment(tmp_path):
    s = setup(tmp_path)
    approve(s)
    received, storage, _ = receiver(s)
    create(storage, "Task", "other")
    raw = command(identifier="other").canonical
    now = int(time.time())
    projection = proof_v2(
        raw,
        actor_id=s.actor,
        receiver_policy_digest_sha256=s.native[0].digest,
        issued_at=now,
        expires_at=now + 60,
    )
    with pytest.raises(SecSWorkDenied):
        received.execute(request_json=raw, projection_json=projection, idempotency_key=KEY)
    assert storage.get_node("Task", "other").properties["version"] == 1
    assert storage.query("EventReceipt") == []


def test_status_missing_receipt_does_not_dispatch_a_write(tmp_path):
    s = setup(tmp_path)
    approve(s)
    received, storage, _ = receiver(s)
    assert execute(s, received, "status") is None
    assert storage.get_node("Task", "child").properties["version"] == 1
    assert storage.query("EventReceipt") == []


def test_shared_registry_lock_serializes_revocation_after_admitted_work(tmp_path):
    s = setup(tmp_path)
    result = approve(s)
    started = threading.Event()
    done = threading.Event()
    errors = []

    def revoker():
        started.set()
        try:
            revoke(s, result)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    with s.provisioning.admission(stable_issuer="secs:test-work"):
        thread = threading.Thread(target=revoker)
        thread.start()
        assert started.wait(1)
        assert not done.wait(0.1)
    thread.join(3)
    assert done.is_set() and not errors


def test_requester_sees_active_write_and_read_registry_does_not_gain_write_access(tmp_path):
    s = setup(tmp_path)
    result = approve(s)
    challenge = s.inbox.wallet_challenge(action(s.public, action="status"))
    reply = s.inbox.execute_wallet(challenge, s.proof, signed(s.holder, challenge))
    assert reply["status"] == "active" and reply["grant_active"] and not reply["authority_issued"]
    assert s.provisioning.registry().assignments == ()
    revoke(s, result)
    challenge = s.inbox.wallet_challenge(action(s.public, action="status"))
    assert (
        s.inbox.execute_wallet(challenge, s.proof, signed(s.holder, challenge))["status"]
        == "revoked"
    )


def test_historical_drafts_and_actions_do_not_acquire_write_activation(tmp_path):
    s = setup(tmp_path)
    p = fields(s)
    with pytest.raises(ValueError):
        OperatorAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v2",
                action="review",
                action_id="f" * 32,
                **p,
            )
        )
    with pytest.raises(ValueError):
        WriteAccessDraft.model_validate({**p["selection"]["write"], "delegation": True})


def test_write_activation_preserves_an_existing_read_assignment(tmp_path):
    from devgraph.auth.monitor_read import AccessDraftV2

    s = setup(tmp_path)
    selected = s.base[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    selected.pop("write_grants", None)
    selected.update(subject_public_key=s.public, installation_id="a" * 32)
    read = AccessDraftV2.model_validate(selected)
    s.base[0] = s.base[0].model_copy(update={"revision": 2, "assignments": (read,)})
    result = approve(s)
    assert s.provisioning.registry().assignments == (read,)
    revoke(s, result)
    assert s.provisioning.registry().assignments == (read,)


def test_mixed_selection_is_rejected_before_any_consent_or_partial_activation(tmp_path):
    s = setup(tmp_path)
    params = fields(s)
    read = s.policies[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    read.pop("write_grants", None)
    read.update(
        schema="devgraph.access-draft.v2", subject_public_key=s.public, installation_id="a" * 32
    )
    params["selection"]["read"] = read
    with pytest.raises(UnauthenticatedError, match="mixed_activation_unavailable"):
        invoke(s, "review", **params)
    with s.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM write_assignments").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM access_assignments").fetchone()[0] == 0


def test_actual_wallet_wasm_can_review_and_activate_a_write_grant(tmp_path):
    import json
    import os
    import subprocess

    driver = os.environ.get("CASTALIA_ACCESS_REQUEST_FIXTURE_DRIVER")
    if not driver:
        pytest.skip("matched Wallet WASM fixture driver required")

    def core(**body):
        run = subprocess.run(
            [os.environ.get("CASTALIA_TEST_NODE", "node"), driver],
            input=json.dumps(body),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return json.loads(run.stdout)

    s = setup(tmp_path)
    public = core(operation="identity")["subject_public_key"]
    s.policies[0] = s.policies[0].model_copy(
        update={"revision": 3, "operator_public_keys": (public,)}
    )
    params = fields_with_wasm(s, core, public)

    def consent(kind, **params):
        a = OperatorAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v3",
                action=kind,
                action_id=secrets.token_hex(16),
                **params,
            )
        )
        challenge = s.service.challenge(public, "b" * 32, a)
        approval = core(operation="approve", challenge=challenge, now=int(time.time()))
        return s.service.execute(**approval)

    reviewed = consent("review", **params)
    activated = consent("approve", review_id=reviewed["review_id"], **params)
    assert activated["status"] == "active" and activated["activation"]["holder_wide"]
    received, storage, _ = receiver(s)
    assert execute(s, received)[0].version == 2
    assert len(storage.query("EventReceipt")) == 1


def fields_with_wasm(s, core, public):
    # The old operator is no longer authorized; obtain inbox data with actual WASM consent.
    a = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v1",
            action="inbox",
            action_id=secrets.token_hex(16),
        )
    )
    challenge = s.service.challenge(public, "b" * 32, a)
    result = s.service.execute(
        **core(operation="approve", challenge=challenge, now=int(time.time()))
    )
    item = result["inbox"]["items"][0]
    value = s.policies[0].ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    value.pop("read_grants", None)
    value.update(subject_public_key=s.public, previous_digest=s.policies[0].ceiling.digest)
    value["write_grants"][0]["resources"][0]["ids"] = ["child"]
    return dict(
        request_id=item["request_id"],
        request_digest_sha256=item["request_digest_sha256"],
        expected_version=1,
        selection={"write": value},
        previous_assignment_digest=item["previous_write_assignment_digest"],
    )


def test_browser_preparation_checks_current_scope_before_native_issuer(tmp_path, monkeypatch):
    from devgraph.ops.browser_credential_work import BrowserCredentialWorkHost

    s = setup(tmp_path)
    received, storage, _ = receiver(s)
    host = BrowserCredentialWorkHost(data_root=tmp_path, receiver=received)
    monkeypatch.setattr(host, "require_origin", lambda origin: None)
    calls = []
    monkeypatch.setattr(host, "_prepare", lambda **kw: calls.append(kw) or {"prepared": True})
    value = dict(
        request=command().canonical.decode(), idempotency_key=KEY, holder_public_key=s.public
    )
    with pytest.raises(SecSWorkDenied):
        host.prepare(value=value, origin="https://example.test")
    assert not calls
    result = approve(s)
    assert host.prepare(value=value, origin="https://example.test") == {"prepared": True}
    assert len(calls) == 1 and len(storage.query("EventReceipt")) == 0
    revoke(s, result)
    with pytest.raises(SecSWorkDenied):
        host.prepare(value=value, origin="https://example.test")
    assert len(calls) == 1
    operations = host.capabilities(value={}, origin="https://example.test")["operations"]
    assert "devgraph.work.patch.v2" in operations and "devgraph.work.patch.v1" not in operations


def test_guarded_http_native_routes_and_status_have_no_legacy_or_bearer_bypass(tmp_path):
    import base64

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from devgraph.api.errors import register_error_handlers
    from devgraph.api.named_work import register_named_work

    s = setup(tmp_path)
    approved = approve(s)
    received, storage, _ = receiver(s)
    app = FastAPI()
    register_error_handlers(app)
    register_named_work(app, SimpleNamespace(named_work=None, named_work_v2=received))
    client = TestClient(app)
    raw = command().canonical
    now = int(time.time())
    projection = proof_v2(
        raw,
        actor_id=s.actor,
        receiver_policy_digest_sha256=s.native[0].digest,
        issued_at=now,
        expires_at=now + 60,
    )
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": KEY,
        "X-Devgraph-Work-Authority": base64.urlsafe_b64encode(projection).rstrip(b"=").decode(),
    }
    assert client.post("/todo-operations/v2/status", content=raw, headers=headers).json() == {
        "state": "unknown"
    }
    assert (
        client.post(
            "/todo-operations/v2",
            content=raw,
            headers={**headers, "Authorization": "Bearer read-cannot-write"},
        ).status_code
        == 403
    )
    result = client.post("/todo-operations/v2", content=raw, headers=headers)
    assert result.status_code == 200, result.text
    receipt = result.json()["receipt"]["receipt_id"]
    status = client.post("/todo-operations/v2/status", content=raw, headers=headers)
    assert status.json()["receipt"]["receipt_id"] == receipt
    assert client.post("/work-operations/v1", content=raw, headers=headers).status_code == 403
    revoke(s, approved)
    assert client.post("/todo-operations/v2", content=raw, headers=headers).status_code == 403
    assert (
        client.post("/todo-operations/v2/status", content=raw, headers=headers).status_code == 403
    )
    assert len(storage.query("EventReceipt")) == 1


def test_unapproved_operator_cannot_probe_native_holder_admission(tmp_path, monkeypatch):
    s = setup(tmp_path)
    params = fields(s)
    act = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v3",
            action="review",
            action_id=secrets.token_hex(16),
            **params,
        )
    )

    def never(grant):
        raise AssertionError("Native holder admission evaluated for an unapproved operator")

    monkeypatch.setattr(s.provisioning, "review_write", never)
    with pytest.raises(UnauthenticatedError, match="operator_required"):
        s.service.challenge("f" * 64, "a" * 32, act)
