"""Real native session subprocess and secS crypto; isolated issuer/graph only."""

import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_access_operator import signed

from devgraph.auth.access_operator import AccessOperator, OperatorPolicy
from devgraph.auth.access_provisioning import ReadGrantProvisioning
from devgraph.auth.access_requests import AccessRequestInbox, RequestAction
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import HolderGrantHost
from devgraph.auth.monitor_read import (
    AccessDraftV2,
    MemberAccessPolicyV2,
    MemberMonitorReadEnrollment,
    compile_monitor_read,
)
from devgraph.auth.session_access import SessionAccessProvider
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task
from devgraph.storage.memory import MemoryGraphStorage


class NativeSession:
    def __init__(self, binary, profile):
        self.child = subprocess.Popen(
            [binary, "--profile-file", str(profile)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def call(self, operation, **fields):
        value = dict(
            schema="castalia.session-agent-command.v1",
            request_id=secrets.token_hex(16),
            body=dict(operation=operation, **fields),
        )
        self.child.stdin.write(json.dumps(value) + "\n")
        self.child.stdin.flush()
        raw = self.child.stdout.readline()
        assert raw, "native session process exited"
        result = json.loads(raw)
        assert result["ok"], result.get("error")
        return result["value"]

    def close(self):
        self.child.terminate()
        self.child.wait(timeout=5)


def write(path, value):
    path.write_bytes(canonical_json(value))
    path.chmod(0o600)
    return path


@pytest.fixture
def session_setup(tmp_path):
    names = (
        "CASTALIA_SESSION_TEST_BINARY",
        "SECS_HOLDER_GRANT_TEST_BINARY",
        "SECS_READ_AUTHORITY_TEST_BINARY",
    )
    if not all(os.environ.get(name) for name in names):
        pytest.skip("matched real native session/holder/read executables required")
    tmp_path.chmod(0o700)
    now = int(time.time())
    issuer, owner = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    owner_public = owner.public_key().public_bytes_raw().hex()
    inbox = AccessRequestInbox(
        path=tmp_path / "inbox.sqlite",
        issuer="fixture",
        receiver="https://fixture.invalid",
        membership_check=lambda proof, public: proof == {},
    )
    ceiling = AccessDraftV2.model_validate(
        dict(
            schema="devgraph.access-draft.v2",
            issuer="fixture",
            receiver=inbox.receiver,
            write_audience="devgraph://receiver-local",
            not_before=now - 1,
            not_after=now + 86400,
            lifetime_seconds=60,
            write_grants=[],
            read_grants=[
                dict(
                    scope="devgraph.work.read",
                    operations=["get", "list"],
                    relationship_types=["parent"],
                    work_kinds=["Task"],
                    include_archived=False,
                )
            ],
        )
    )
    policies = [
        OperatorPolicy.model_validate(
            dict(
                schema="devgraph.access-operator-policy.v1",
                revision=1,
                operator_public_keys=[owner_public],
                ceiling=ceiling,
            )
        )
    ]
    base = MemberAccessPolicyV2.model_validate(
        dict(schema="devgraph.member-access-policy.v2", revision=1, ceiling=ceiling, assignments=[])
    )
    provisioning = ReadGrantProvisioning(inbox=inbox, members=lambda: base)
    operator = AccessOperator(inbox=inbox, policy=lambda: policies[0], provisioning=provisioning)
    policy = compile_monitor_read(ceiling)
    keyfile = tmp_path / "issuer.key"
    keyfile.write_text(issuer.private_bytes_raw().hex())
    keyfile.chmod(0o600)
    read_policy = SecsReadAuthority.policy_for(
        policy, key_id="fixture-key", public_key=issuer.public_key().public_bytes_raw().hex()
    )
    authority = SecsReadAuthority(
        binary=Path(os.environ[names[2]]),
        issuer_key_file=keyfile,
        policy_file=write(tmp_path / "authority.json", read_policy),
    )
    enrollment = MemberMonitorReadEnrollment(
        path=tmp_path / "enrollment.sqlite",
        policy=lambda: policy,
        membership_check=lambda proof, public: proof == {},
        authority=authority,
        members=provisioning.registry,
    )
    binary = Path(os.environ[names[1]])
    holder = HolderGrantHost(
        enrollment=enrollment,
        binary=binary,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        service="service:fixture",
        grant_schema="secs.holder-grant.v2",
    )
    provider = SessionAccessProvider(operator=operator, holder=holder, profile_digest="ab" * 32)
    pin = dict(
        issuer=holder.pin["issuer"],
        key_id=holder.pin["key_id"],
        public_key=holder.pin["public_key"],
        receiver=inbox.receiver,
        audience="devgraph",
    )
    native = NativeSession(
        os.environ[names[0]],
        write(
            tmp_path / "session-profile.json",
            dict(
                schema="castalia.session-agent-profile.v1",
                provider=holder.pin,
                receiver=pin,
                maximum_session_seconds=3600,
            ),
        ),
    )
    identity = native.call("create_session", expires_at=now + 3600)
    identity = dict(
        session_handle=identity["session_handle"],
        public_key=identity["holder_public_key"],
        installation_id=identity["installation_id"],
    )
    request_id, client = secrets.token_hex(16), secrets.token_hex(16)
    request = RequestAction.model_validate(
        dict(
            schema="devgraph.access-request-action.v1",
            action="submit",
            request_id=request_id,
            subject_public_key=identity["public_key"],
            installation_id=identity["installation_id"],
            intent=None,
        )
    )
    challenge = inbox.wallet_challenge(request)
    approval = native.call(
        "approve_member_request", session_handle=identity["session_handle"], challenge=challenge
    )
    inbox.execute_wallet(approval["challenge"], {}, approval["signature"])
    draft = ceiling.model_copy(
        update=dict(
            subject_public_key=identity["public_key"],
            installation_id=identity["installation_id"],
            read_grants=(
                ceiling.read_grants[0].model_copy(
                    update=dict(operations=("get",), work_ids=("Task/allowed",))
                ),
            ),
        )
    )
    permissions = provider._permissions(draft)
    review = provider.prepare(
        client,
        request_id,
        identity,
        permissions,
        now + 300,
        dict(session_label="Fixture session", purpose="Read one task"),
    )
    storage = MemoryGraphStorage()
    repository = WorkObjectRepository(storage)
    repository.create(Task(id="allowed", title="Allowed"))
    repository.create(Task(id="hidden", title="Hidden"))
    value = dict(
        provider=provider,
        native=native,
        identity=identity,
        review=review,
        client=client,
        owner=owner,
        owner_public=owner_public,
        policies=policies,
        storage=storage,
        audit=AuditLog(),
    )
    try:
        yield value
    finally:
        native.close()


def issue(s):
    p, r = s["provider"], s["review"]
    owner_challenge = p.owner_challenge(
        s["client"], r["request_handle"], s["owner_public"], "aa" * 16
    )
    result = p.submit(
        s["client"], r["request_handle"], owner_challenge, signed(s["owner"], owner_challenge)
    )
    assert result["state"] == "submitting" and result["credential_handle"] is None
    challenge = p.begin_enrollment(s["client"], r["request_handle"])
    proof = s["native"].call(
        "approve_enrollment", session_handle=s["identity"]["session_handle"], challenge=challenge
    )
    result = p.complete_enrollment(
        s["client"], r["request_handle"], proof["challenge"], proof["signature"]
    )
    assert result["state"] == "issued"
    root = p.reconcile(s["client"], r["request_handle"], internal_root=True)
    s["native"].call(
        "install_grant",
        session_handle=s["identity"]["session_handle"],
        grant=root["grant"],
        reviewed_expiry=r["manifest"]["expires_at"],
        reviewed_permissions=r["manifest"]["permissions"],
    )
    return result


def test_real_session_issue_call_replay_scope_and_no_owner_key_borrowing(session_setup):
    s = session_setup
    assert s["identity"]["public_key"] != s["owner_public"]
    issued = issue(s)
    assert issued == s["provider"].reconcile(s["client"], s["review"]["request_handle"])
    call = dict(
        operation="devgraph.work.get",
        resource="https://work.zenith-research.ca/devgraph",
        parameters=dict(kind="Task", work_id="allowed"),
    )
    envelope = s["native"].call(
        "present_call", session_handle=s["identity"]["session_handle"], call=call
    )
    value = s["provider"].execute(
        s["client"],
        s["review"]["request_handle"],
        secrets.token_hex(16),
        envelope,
        storage=s["storage"],
        audit_log=s["audit"],
    )
    assert value["result"]["title"] == "Allowed"
    assert set(value) == {"schema", "request_id", "provider_service", "operation", "result"}
    with pytest.raises(UnauthenticatedError):
        s["provider"].execute(
            s["client"],
            s["review"]["request_handle"],
            secrets.token_hex(16),
            envelope,
            storage=s["storage"],
            audit_log=s["audit"],
        )

    call["parameters"]["work_id"] = "hidden"
    envelope = s["native"].call(
        "present_call", session_handle=s["identity"]["session_handle"], call=call
    )
    with pytest.raises(KeyError):
        s["provider"].execute(
            s["client"],
            s["review"]["request_handle"],
            secrets.token_hex(16),
            envelope,
            storage=s["storage"],
            audit_log=s["audit"],
        )


def test_owner_approval_cannot_survive_expiry_edit_or_policy_change(session_setup):
    s = session_setup
    provider, review, client = s["provider"], s["review"], s["client"]
    handle = review["request_handle"]
    original = provider.owner_challenge(client, handle, s["owner_public"], "aa" * 16)
    edited = provider.update_expiry(client, handle, review["manifest"]["expires_at"] - 60)
    assert edited["manifest"]["review_revision"] == 2
    with pytest.raises(UnauthenticatedError):
        provider.submit(client, handle, original, signed(s["owner"], original))
    current = provider.owner_challenge(client, handle, s["owner_public"], "aa" * 16)
    stranger = Ed25519PrivateKey.generate()
    with pytest.raises(UnauthenticatedError):
        provider.submit(client, handle, current, signed(stranger, current))
    s["policies"][0] = s["policies"][0].model_copy(update={"revision": 2})
    with pytest.raises(UnauthenticatedError):
        provider.submit(client, handle, current, signed(s["owner"], current))
    with provider.inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone()[0] == 0


def test_activation_failure_rolls_back_assignment_and_can_reuse_exact_original(
    session_setup, monkeypatch
):
    s = session_setup
    provider, handle = s["provider"], s["review"]["request_handle"]
    challenge = provider.owner_challenge(s["client"], handle, s["owner_public"], "aa" * 16)
    provisioning = provider.operator.provisioning
    activate = provisioning.activate

    def fail_after_assignment(*args, **kwargs):
        activate(*args, **kwargs)
        raise RuntimeError("fixture_activation_failure")

    with monkeypatch.context() as patch:
        patch.setattr(provisioning, "activate", fail_after_assignment)
        with pytest.raises(RuntimeError, match="fixture_activation_failure"):
            provider.submit(s["client"], handle, challenge, signed(s["owner"], challenge))
    with provider.inbox._database() as db:
        assert (
            provisioning.current(db, s["identity"]["public_key"], s["identity"]["installation_id"])
            is None
        )
        assert db.execute("SELECT count(*) FROM access_decisions").fetchone()[0] == 0
    result = provider.submit(s["client"], handle, challenge, signed(s["owner"], challenge))
    assert result["state"] == "submitting"
