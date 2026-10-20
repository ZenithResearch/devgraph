import base64
import secrets
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_access_requests import action, setup, submit

from devgraph.auth.access_approval import DOMAIN
from devgraph.auth.access_operator import AccessOperator, OperatorAction, OperatorPolicy
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import AccessDraftV2


def fixture(tmp_path):
    inbox, holder, public, proof, _ = setup(tmp_path)
    submit(inbox, holder, public, proof)
    operator = Ed25519PrivateKey.generate()
    key = operator.public_key().public_bytes_raw().hex()
    now = int(datetime.now(timezone.utc).timestamp())
    ceiling = AccessDraftV2.model_validate(
        dict(
            schema="devgraph.access-draft.v2",
            issuer="fixture",
            receiver="https://example.test",
            write_audience="devgraph://receiver-local",
            not_before=now - 1,
            not_after=now + 3600,
            lifetime_seconds=300,
            read_grants=[
                dict(
                    scope="devgraph.work.read",
                    operations=["list", "get"],
                    relationship_types=["parent"],
                    work_kinds=["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"],
                    include_archived=False,
                )
            ],
            write_grants=[
                dict(operation="devgraph.work.progress.set.v2", resources=[dict(kind="Todo")])
            ],
        )
    )
    policy = [
        OperatorPolicy.model_validate(
            dict(
                schema="devgraph.access-operator-policy.v1",
                revision=1,
                operator_public_keys=[key],
                ceiling=ceiling,
            )
        )
    ]
    service = AccessOperator(inbox=inbox, policy=lambda: policy[0])
    return inbox, service, holder, public, proof, operator, key, policy


def signed(key, challenge):
    return (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )


def call(service, key, public, kind, **fields):
    action = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v1",
            action=kind,
            action_id=secrets.token_hex(16),
            **fields,
        )
    )
    challenge = service.challenge(public, "a" * 32, action)
    return service.execute(challenge, signed(key, challenge))


def selection(policy, public):
    c = policy.ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    read = {
        **c,
        "subject_public_key": public,
        "installation_id": "a" * 32,
        "previous_digest": policy.ceiling.digest,
    }
    read.pop("write_grants", None)
    write = {**c, "subject_public_key": public, "previous_digest": policy.ceiling.digest}
    write.pop("read_grants", None)
    return dict(read=read, write=write)


def test_member_can_request_but_cannot_inspect_operator_inbox(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    request = inbox.wallet_challenge(action(public, identifier="c" * 32))
    assert inbox.execute_wallet(request, proof, signed(holder, request))["status"] == "pending"
    with pytest.raises(UnauthenticatedError):
        service.challenge(
            public,
            "a" * 32,
            OperatorAction.model_validate(
                dict(
                    schema="devgraph.access-operator-action.v1", action="inbox", action_id="e" * 32
                )
            ),
        )
    challenge = service.challenge(
        key,
        "a" * 32,
        OperatorAction.model_validate(
            dict(schema="devgraph.access-operator-action.v1", action="inbox", action_id="f" * 32)
        ),
    )
    assert "ceiling" not in challenge["details"] and "inbox" not in challenge["details"]
    with pytest.raises(UnauthenticatedError):
        service.execute(challenge, signed(holder, challenge))
    result = service.execute(challenge, signed(op, challenge))
    assert result["inbox"]["total"] == 2 and result["authority_issued"] is False


def test_exact_review_approval_retry_and_pending_activation(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    item = call(service, op, key, "inbox")["inbox"]["items"][0]
    params = dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
        selection=selection(policy[0], public),
    )
    review = call(service, op, key, "review", **params)
    assert set(review["plans"]) == {"read", "write"}
    with pytest.raises(ValueError):
        OperatorAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v1",
                action="approve",
                action_id="f" * 32,
                **params,
            )
        )
    action_ = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v1",
            action="approve",
            action_id="d" * 32,
            review_id=review["review_id"],
            **params,
        )
    )
    challenge = service.challenge(key, "a" * 32, action_)
    result = service.execute(challenge, signed(op, challenge))
    assert result["status"] == "approved_pending_activation" and result["authority_issued"] is False
    assert service.execute(challenge, signed(op, challenge)) == result
    assert call(service, op, key, "inbox")["inbox"]["total"] == 0
    with pytest.raises(UnauthenticatedError, match="request_changed"):
        call(service, op, key, "review", **params)
    status = inbox.wallet_challenge(action(public, action="status"))
    assert inbox.execute_wallet(status, proof, signed(holder, status))["authority_issued"] is False


def test_policy_change_revocation_and_conflicting_decisions(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    item = call(service, op, key, "inbox")["inbox"]["items"][0]
    params = dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
    )
    a = OperatorAction.model_validate(
        dict(
            schema="devgraph.access-operator-action.v1",
            action="deny",
            action_id="e" * 32,
            reason="Outside project scope",
            **params,
        )
    )
    stale = service.challenge(key, "a" * 32, a)
    original = policy[0]
    policy[0] = original.model_copy(update={"revision": 2})
    with pytest.raises(UnauthenticatedError, match="operator_policy_changed"):
        service.execute(stale, signed(op, stale))
    # Admit a valid call to retain the highest policy revision.
    call(service, op, key, "inbox")
    policy[0] = original
    with pytest.raises(UnauthenticatedError, match="rollback"):
        call(service, op, key, "inbox")
    policy[0] = original.model_copy(update={"revision": 3})
    assert (
        call(service, op, key, "deny", reason="Outside project scope", **params)["status"]
        == "denied"
    )
    with pytest.raises(UnauthenticatedError, match="request_changed"):
        call(service, op, key, "deny", reason="Different decision", **params)


def test_selection_cannot_widen_or_drop_bindings(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    item = call(service, op, key, "inbox")["inbox"]["items"][0]
    params = dict(
        request_id=item["request_id"],
        expected_version=1,
        request_digest_sha256=item["request_digest_sha256"],
    )
    selected = selection(policy[0], public)
    selected["read"]["read_grants"][0]["include_archived"] = True
    with pytest.raises(ValueError, match="amplification"):
        call(service, op, key, "review", selection=selected, **params)
    selected = selection(policy[0], public)
    selected["write"]["installation_id"] = "a" * 32
    with pytest.raises(UnauthenticatedError, match="unsupported_write_binding"):
        call(service, op, key, "review", selection=selected, **params)
    assert call(service, op, key, "inbox")["inbox"]["total"] == 1


def test_status_recovery_never_creates_a_missing_operator_decision(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    result = call(service, op, key, "status", original_action_id="e" * 32)
    assert result["status"] == "unknown" and result["result"] is None
    assert call(service, op, key, "inbox")["inbox"]["total"] == 1


def test_actual_wasm_fixture_signature_and_canonical_bytes():
    import hashlib
    import json
    from pathlib import Path

    from devgraph.auth.access_approval import verify_wallet_approval

    root = Path(__file__).parents[2] / "docs/contracts/castalia-access-request/v1/vector.json"
    vector = json.loads(root.read_text())
    transcript = DOMAIN + canonical_json(vector["challenge"])
    assert transcript.hex() == vector["signing_bytes_hex"]
    assert hashlib.sha256(transcript).hexdigest() == vector["signing_sha256"]
    assert (
        verify_wallet_approval(
            vector["challenge"],
            vector["signature"],
            issuer="fixture",
            receiver="https://example.test",
            current=100,
        ).operation
        == "access.request"
    )


def test_operator_http_is_opt_in_signed_and_never_bearer_admin(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from devgraph.api.access_operator import register_access_operator

    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    app = FastAPI()
    register_access_operator(app, service)
    client = TestClient(app, base_url="https://example.test")
    body = dict(
        subject_public_key=key,
        installation_id="a" * 32,
        action=dict(
            schema="devgraph.access-operator-action.v1", action="inbox", action_id="e" * 32
        ),
    )
    challenge = client.post("/devgraph/access-operator/v1/challenges", json=body)
    assert challenge.status_code == 200
    assert client.get("/devgraph/access-operator/v1").status_code == 404
    assert (
        client.post(
            "/devgraph/access-operator/v1/actions",
            json={"challenge": challenge.json(), "signature": signed(op, challenge.json())},
            headers={"Authorization": "Bearer owner-read"},
        ).status_code
        == 401
    )
    result = client.post(
        "/devgraph/access-operator/v1/actions",
        json={"challenge": challenge.json(), "signature": signed(op, challenge.json())},
    )
    assert result.status_code == 200 and result.json()["inbox"]["total"] == 1
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/monitor/access/").status_code == 200
    assert client.get("/monitor/access-assets/core.mjs").status_code == 200
    assert client.get("/monitor/access-assets/policy.mjs").status_code == 200
    assert client.get("/monitor/access-assets/unknown").status_code == 404
    assert (
        TestClient(app).post("/devgraph/access-operator/v1/challenges", json=body).status_code
        == 401
    )
    assert (
        client.post(
            "/devgraph/access-operator/v1/challenges", json={**body, "operator": True}
        ).status_code
        == 401
    )


def test_rejected_revoked_operator_cannot_restore_older_policy(tmp_path):
    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    call(service, op, key, "inbox")
    old = policy[0]
    policy[0] = old.model_copy(update={"revision": 2, "operator_public_keys": ()})
    with pytest.raises(UnauthenticatedError):
        call(service, op, key, "inbox")
    policy[0] = old
    with pytest.raises(UnauthenticatedError, match="rollback"):
        call(service, op, key, "inbox")
    with inbox._database() as db:
        assert db.execute("SELECT revision FROM access_operator_head").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM access_operator_denials").fetchone()[0] >= 2


def test_operator_policy_expiry_blocks_inbox_and_decisions(tmp_path):
    from datetime import timedelta

    inbox, service, holder, public, proof, op, key, policy = fixture(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    a = OperatorAction.model_validate(
        dict(schema="devgraph.access-operator-action.v1", action="inbox", action_id="f" * 32)
    )
    with pytest.raises(UnauthenticatedError, match="operator_policy_expired"):
        service.challenge(key, "a" * 32, a, now=future)


def test_browser_checkbox_selection_keeps_exact_server_signing_bytes(tmp_path):
    """UI ordering must not cause the receiver to rewrite a reviewed request."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from devgraph.auth.access_operator import GrantSelection

    if shutil.which("node") is None:
        pytest.skip("node required for browser/server contract check")
    _, _, _, public, _, _, _, policies = fixture(tmp_path)
    ceiling = policies[0].ceiling
    now = ceiling.not_before + 1
    module = (
        Path(__file__).resolve().parents[2]
        / "src/devgraph/frontend/static/access/core.mjs"
    ).as_uri()
    script = (
        "import{makeSelection}from " + json.dumps(module) + ";"
        "import{readFileSync}from'node:fs';"
        "const args=JSON.parse(readFileSync(0,'utf8'));"
        "console.log(JSON.stringify(makeSelection(args)));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(
            dict(
                ceiling=ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True),
                ceilingDigest=ceiling.digest,
                recipient=dict(subject_public_key=public, installation_id="a" * 32),
                mode="read",
                workKinds=sorted(ceiling.read_grants[0].work_kinds),
                readOperations=["get", "list"],
                expiresAt=now + 100,
                now=now,
            )
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    selected = json.loads(result.stdout)
    parsed = GrantSelection.model_validate(selected)
    assert canonical_json(selected) == canonical_json(parsed)
