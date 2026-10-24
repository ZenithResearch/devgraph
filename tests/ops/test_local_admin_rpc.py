import base64
import secrets
import sqlite3
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.api.access_operator import register_access_operator
from devgraph.auth.access_approval import DOMAIN
from devgraph.auth.access_operator import AccessOperator, OperatorPolicy
from devgraph.auth.access_requests import AccessRequestInbox, RequestAction, signable_access_request
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.monitor_read import AccessDraftV2
from devgraph.ops.local_admin_rpc import dispatch


def fixture(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw().hex()
    inbox = AccessRequestInbox(
        path=tmp_path / "inbox.sqlite",
        issuer="fixture",
        receiver="https://example.test",
        membership_check=lambda p, k: p == {"holder": k},
    )
    holder = Ed25519PrivateKey.generate()
    member = holder.public_key().public_bytes_raw().hex()
    action = RequestAction.model_validate(
        dict(
            schema="devgraph.access-request-action.v1",
            action="submit",
            request_id="a" * 32,
            subject_public_key=member,
            installation_id="b" * 32,
            intent=None,
        )
    )
    ch = inbox.challenge(action)
    inbox.execute(
        ch,
        {"holder": member},
        base64.urlsafe_b64encode(holder.sign(signable_access_request(ch))).rstrip(b"=").decode(),
    )
    now = int(time.time())
    ceiling = AccessDraftV2.model_validate(
        dict(
            schema="devgraph.access-draft.v2",
            issuer="fixture",
            receiver=inbox.receiver,
            write_audience="devgraph://receiver-local",
            not_before=now - 1,
            not_after=now + 3600,
            lifetime_seconds=300,
            read_grants=[
                dict(
                    scope="devgraph.work.read",
                    operations=["list", "get"],
                    relationship_types=["parent"],
                    work_kinds=["Task"],
                    include_archived=False,
                )
            ],
            write_grants=[],
        )
    )
    policy = OperatorPolicy.model_validate(
        dict(
            schema="devgraph.access-operator-policy.v1",
            revision=1,
            operator_public_keys=[public],
            ceiling=ceiling,
        )
    )
    service = AccessOperator(inbox=inbox, policy=lambda: policy)
    app = FastAPI()
    register_access_operator(app, service)
    return service, key, public, TestClient(app, base_url=inbox.receiver)


def identity(public, action="inbox", **fields):
    return dict(
        subject_public_key=public,
        installation_id="b" * 32,
        action=dict(
            schema="devgraph.access-operator-action.v1",
            action=action,
            action_id=secrets.token_hex(16),
            **fields,
        ),
    )


def proof(key, ch):
    return dict(
        challenge=ch,
        signature=base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(ch)))
        .rstrip(b"=")
        .decode(),
    )


def test_same_operator_interoperates_both_directions_with_https(tmp_path):
    service, key, public, http = fixture(tmp_path)
    ch = http.post("/devgraph/access-operator/v1/challenges", json=identity(public)).json()
    status, value = dispatch(service, "execute", proof(key, ch))
    assert status == 200 and value["inbox"]["total"] == 1
    status, ch = dispatch(service, "challenge", identity(public))
    assert status == 200 and "inbox" not in ch["details"]
    result = http.post("/devgraph/access-operator/v1/actions", json=proof(key, ch))
    assert result.status_code == 200 and result.json()["inbox"]["total"] == 1
    assert service is http.app.state.access_operator


def test_local_decision_recovers_over_https_without_second_decision(tmp_path):
    service, key, public, http = fixture(tmp_path)
    _, ch = dispatch(service, "challenge", identity(public))
    _, value = dispatch(service, "execute", proof(key, ch))
    item = value["inbox"]["items"][0]
    body = identity(
        public,
        "deny",
        request_id=item["request_id"],
        request_digest_sha256=item["request_digest_sha256"],
        expected_version=1,
        reason="Synthetic transport qualification",
    )
    _, ch = dispatch(service, "challenge", body)
    status, original = dispatch(service, "execute", proof(key, ch))
    assert status == 200 and original["status"] == "denied"
    ch = http.post(
        "/devgraph/access-operator/v1/challenges",
        json=identity(public, "status", original_action_id=body["action"]["action_id"]),
    ).json()
    recovered = http.post("/devgraph/access-operator/v1/actions", json=proof(key, ch)).json()
    assert recovered["status"] == "reconciled" and recovered["result"] == original
    with sqlite3.connect(service.inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_operator_results").fetchone()[0] == 3
        assert (
            db.execute(
                "SELECT count(*) FROM access_operator_audit WHERE operation=?", ("deny",)
            ).fetchone()[0]
            == 1
        )


def test_no_local_identity_bypass_or_arbitrary_command(tmp_path):
    service, key, public, _ = fixture(tmp_path)
    other = Ed25519PrivateKey.generate()
    assert (
        dispatch(service, "challenge", identity(other.public_key().public_bytes_raw().hex()))[0]
        == 401
    )
    _, ch = dispatch(service, "challenge", identity(public))
    assert dispatch(service, "execute", proof(other, ch))[0] == 401
    signed = proof(key, ch)
    signed["challenge"]["details"]["action"]["action"] = "approve"
    assert dispatch(service, "execute", signed)[0] == 401
    assert dispatch(service, "challenge", {**identity(public), "executable": "/bin/sh"})[0] == 400
    assert dispatch(service, "deploy", {})[0] == 400


def test_storage_failure_is_unknown_not_success():
    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("private path")

    assert dispatch(Broken(), "execute", {"challenge": {}, "signature": "x"}) == (
        503,
        {"error": "operator_result_unknown"},
    )
