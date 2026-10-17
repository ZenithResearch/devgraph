"""Disposable real membership and holder signatures; no grants/live data."""

import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.native_read.test_http import fixture

from devgraph.api.access_requests import register_access_request_routes
from devgraph.auth.access_requests import AccessRequestInbox, RequestAction, signable_access_request
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_read import _seconds


def setup(tmp_path, **kwargs):
    owner, public, proof, revoked, _, enrollment = fixture(tmp_path)
    inbox = AccessRequestInbox(
        path=tmp_path / "inbox.sqlite",
        issuer="fixture",
        receiver="https://example.test",
        membership_check=enrollment.membership_check,
        **kwargs,
    )
    return inbox, owner, public, proof, revoked


def action(public, identifier="b" * 32, **changes):
    value = dict(
        schema="devgraph.access-request-action.v1",
        action="submit",
        request_id=identifier,
        subject_public_key=public,
        installation_id="a" * 32,
        intent=None,
    )
    return RequestAction.model_validate({**value, **changes})


def signed(owner, challenge):
    return (
        base64.urlsafe_b64encode(owner.sign(signable_access_request(challenge)))
        .rstrip(b"=")
        .decode()
    )


def submit(inbox, owner, public, proof, **kwargs):
    challenge = inbox.challenge(action(public, **kwargs))
    return inbox.execute(challenge, proof, signed(owner, challenge))


def test_durable_exact_retry_recovery_and_atomic_audit(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    challenge = inbox.challenge(action(public))
    sig = signed(owner, challenge)
    first = inbox.execute(challenge, proof, sig)
    assert first["status"] == "pending" and first["authority_issued"] is False
    import hashlib

    from devgraph.auth.delegated_contract import canonical_json

    assert (
        first["request_digest_sha256"]
        == hashlib.sha256(canonical_json(challenge["request"])).hexdigest()
    )
    restarted = AccessRequestInbox(
        path=inbox.path,
        issuer="fixture",
        receiver="https://example.test",
        membership_check=inbox.membership_check,
    )
    assert restarted.execute(challenge, proof, sig) == first
    assert submit(restarted, owner, public, proof) == first
    status = restarted.challenge(action(public, action="status"))
    assert restarted.execute(status, proof, signed(owner, status)) == first
    missing = restarted.challenge(action(public, identifier="c" * 32, action="status"))
    assert restarted.execute(missing, proof, signed(owner, missing))["status"] == "unknown"
    with sqlite3.connect(inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_requests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM access_request_audit").fetchone()[0] == 1
    assert proof["issuerSignature"].encode() not in inbox.path.read_bytes()
    with pytest.raises(ValueError, match="binding changed"):
        AccessRequestInbox(
            path=inbox.path,
            issuer="different",
            receiver="https://example.test",
            membership_check=inbox.membership_check,
        )


def test_real_membership_revocation_wrong_holder_and_tampering(tmp_path):
    inbox, owner, public, proof, revoked = setup(tmp_path)
    challenge = inbox.challenge(action(public))
    for invalid in (
        {**challenge, "receiver": "https://wrong.test"},
        {**challenge, "request": {**challenge["request"], "installation_id": "f" * 32}},
        {**challenge, "credential": "injected"},
    ):
        with pytest.raises(UnauthenticatedError):
            inbox.execute(invalid, proof, signed(owner, invalid))
    with pytest.raises(UnauthenticatedError):
        inbox.execute(challenge, proof, signed(Ed25519PrivateKey.generate(), challenge))
    with pytest.raises(UnauthenticatedError, match="membership_required"):
        inbox.execute(challenge, {**proof, "status": "forged"}, signed(owner, challenge))
    submit(inbox, owner, public, proof)
    revoked.add(proof["membershipId"])
    with pytest.raises(UnauthenticatedError, match="membership_required"):
        inbox.execute(challenge, proof, signed(owner, challenge))
    with sqlite3.connect(inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_requests").fetchone()[0] == 1


def test_binding_conflicts_expiry_and_status_only(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    submit(inbox, owner, public, proof)
    intent = dict(
        read_grants=[
            dict(
                scope="devgraph.work.read",
                operations=["get"],
                relationship_types=["parent"],
                work_kinds=["Task"],
                arena_ids=["zenith"],
                include_archived=False,
            )
        ]
    )
    other = inbox.challenge(action(public, intent=intent))
    with pytest.raises(UnauthenticatedError, match="request_conflict"):
        inbox.execute(other, proof, signed(owner, other))
    wrong_device = inbox.challenge(action(public, action="status", installation_id="d" * 32))
    with pytest.raises(UnauthenticatedError, match="request_unavailable"):
        inbox.execute(wrong_device, proof, signed(owner, wrong_device))
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    stale = inbox.challenge(action(public, identifier="e" * 32), now=now)
    with pytest.raises(UnauthenticatedError):
        inbox.execute(stale, proof, signed(owner, stale), now=now + timedelta(seconds=120))
    later = now + timedelta(days=8)
    status = inbox.challenge(action(public, action="status"), now=later)
    assert inbox.execute(status, proof, signed(owner, status), now=later)["status"] == "expired"
    with pytest.raises(ValueError):
        action(public, action="status", intent=intent)


def test_requested_scope_never_installs_authority(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    intent = dict(
        write_grants=[
            dict(
                operation="devgraph.work.progress.set.v2",
                resources=[dict(kind="Todo", ids=["one"])],
            )
        ]
    )
    assert submit(inbox, owner, public, proof, intent=intent)["authority_issued"] is False
    with pytest.raises(ValueError):
        action(
            public,
            intent=dict(
                write_grants=[dict(operation="grant.admin", resources=[dict(kind="Task")])]
            ),
        )
    assert not hasattr(inbox, "approve") and not hasattr(inbox, "issue")


def test_quota_and_concurrent_retries(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path, max_per_member=1)
    challenge = inbox.challenge(action(public))
    sig = signed(owner, challenge)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: inbox.execute(challenge, proof, sig), range(4)))
    assert all(r == results[0] for r in results)
    with pytest.raises(UnauthenticatedError, match="try_later"):
        submit(inbox, owner, public, proof, identifier="c" * 32)
    with sqlite3.connect(inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_request_audit").fetchone()[0] == 1


def test_storage_abort_has_no_partial_request_or_audit(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    with sqlite3.connect(inbox.path) as db:
        db.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON access_request_audit "
            "BEGIN SELECT RAISE(ABORT, 'fixture'); END"
        )
    challenge = inbox.challenge(action(public))
    sig = signed(owner, challenge)
    with pytest.raises(sqlite3.IntegrityError):
        inbox.execute(challenge, proof, sig)
    with sqlite3.connect(inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_requests").fetchone()[0] == 0
        assert db.execute("SELECT used FROM access_challenges").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_audit")
    assert inbox.execute(challenge, proof, sig)["status"] == "pending"


def test_challenge_budget_and_private_storage(tmp_path):
    inbox, _, public, _, _ = setup(tmp_path)
    for _ in range(8):
        inbox.challenge(action(public))
    with pytest.raises(UnauthenticatedError, match="try_later"):
        inbox.challenge(action(public))
    assert inbox.path.stat().st_mode & 0o777 == 0o600
    unsafe = tmp_path / "unsafe.sqlite"
    unsafe.symlink_to(inbox.path)
    with pytest.raises(ValueError, match="unsafe"):
        AccessRequestInbox(
            path=unsafe,
            issuer="fixture",
            receiver="https://example.test",
            membership_check=inbox.membership_check,
        )


def test_https_closed_http_contract_and_no_bearer_or_admin_routes(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    app = FastAPI()
    register_access_request_routes(app, inbox)
    client = TestClient(app, base_url="https://example.test")
    path = "/devgraph/access-requests/"
    body = {"request": action(public).model_dump(mode="json", by_alias=True)}
    response = client.post(path + "challenges", json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    challenge = response.json()
    approval = dict(challenge=challenge, proof=proof, signature=signed(owner, challenge))
    response = client.post(path + "actions", json=approval)
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert (
        client.post(
            path + "challenges", json=body, headers={"Authorization": "Bearer read-only"}
        ).status_code
        == 401
    )
    assert (
        TestClient(app)
        .post(path + "challenges", json=body, headers={"X-Forwarded-Proto": "https"})
        .status_code
        == 401
    )
    assert (
        client.post(path + "challenges", content='{"request":{},"request":{}}').status_code == 401
    )
    assert client.post(path + "actions", content="x" * 65537).status_code == 401
    assert client.post(path + "challenges", json={**body, "admin": True}).status_code == 401
    assert (
        TestClient(app, base_url="https://wrong.test")
        .post(path + "challenges", json=body)
        .status_code
        == 401
    )
    assert client.get(path).status_code == 404
    assert client.post(path + "approve", json=approval).status_code == 404
    assert client.post("/devgraph/todo-operations/v2", json=approval).status_code == 404
    with sqlite3.connect(inbox.path) as db:
        assert db.execute("SELECT count(*) FROM access_requests").fetchone()[0] == 1
    assert _seconds(None) >= challenge["issued_at"]


def test_operator_projection_pagination_counts_and_scope(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    for identifier in ("b" * 32, "c" * 32, "d" * 32):
        submit(inbox, owner, public, proof, identifier=identifier)
    page = inbox.pending(limit=2)
    assert page["total"] == 3 and len(page["items"]) == 2
    assert all(item["intent"] is None for item in page["items"])
    assert all("proof" not in item and "credential" not in item for item in page["items"])
    next_page = inbox.pending(limit=2, after_id=page["next_cursor"])
    assert next_page["total"] == 3 and len(next_page["items"]) == 1
    assert next_page["next_cursor"] is None
    for invalid in (True, 0, 101):
        with pytest.raises(ValueError):
            inbox.pending(limit=invalid)


def test_distinct_valid_member_cannot_read_or_replace_another_request(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path)
    submit(inbox, owner, public, proof)
    other_dir = tmp_path / "other"
    other_dir.mkdir(mode=0o700)
    other_owner, other_public, other_proof, _, _, other_enrollment = fixture(other_dir)
    first_check = inbox.membership_check
    inbox.membership_check = lambda p, k: (
        first_check(p, k) or other_enrollment.membership_check(p, k)
    )
    for operation in ("submit", "status"):
        challenge = inbox.challenge(action(other_public, action=operation))
        with pytest.raises(UnauthenticatedError, match="request_unavailable"):
            inbox.execute(challenge, other_proof, signed(other_owner, challenge))
    assert inbox.pending()["total"] == 1


def test_global_capacity_and_http_storage_error_recovery(tmp_path):
    inbox, owner, public, proof, _ = setup(tmp_path, max_requests=1)
    app = FastAPI()
    register_access_request_routes(app, inbox)
    client = TestClient(app, base_url="https://example.test")
    challenge = inbox.challenge(action(public))
    approval = dict(challenge=challenge, proof=proof, signature=signed(owner, challenge))
    with sqlite3.connect(inbox.path) as db:
        db.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON access_request_audit "
            "BEGIN SELECT RAISE(ABORT, 'private fixture detail'); END"
        )
    response = client.post("/devgraph/access-requests/actions", json=approval)
    assert response.status_code == 503
    assert response.json() == {"error": "access_request_unavailable"}
    assert inbox.pending()["total"] == 0
    with sqlite3.connect(inbox.path) as db:
        db.execute("DROP TRIGGER fail_audit")
    assert (
        client.post("/devgraph/access-requests/actions", json=approval).json()["status"]
        == "pending"
    )
    with pytest.raises(UnauthenticatedError, match="try_later"):
        submit(inbox, owner, public, proof, identifier="c" * 32)


@pytest.mark.parametrize("kind", ["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"])
def test_existing_six_kind_read_contract_is_reused(tmp_path, kind):
    inbox, owner, public, proof, _ = setup(tmp_path)
    intent = dict(
        read_grants=[
            dict(
                scope="devgraph.work.read",
                operations=["get"],
                relationship_types=["parent"],
                work_kinds=[kind],
                include_archived=False,
                arena_ids=["zenith"],
            )
        ]
    )
    assert submit(inbox, owner, public, proof, intent=intent)["status"] == "pending"
    assert inbox.pending()["items"][0]["intent"]["read_grants"][0]["work_kinds"] == [kind]


def test_published_schemas_and_signing_vector_match_implementation():
    import hashlib
    from pathlib import Path

    from devgraph.auth.access_requests import Challenge

    root = Path(__file__).parents[2] / "docs/contracts/devgraph-access-request/v1"
    for name, model in [("action", RequestAction), ("challenge", Challenge)]:
        assert json.loads((root / (name + ".schema.json")).read_text()) == model.model_json_schema(
            by_alias=True
        )
    vector = json.loads((root / "approval-vector.json").read_text())
    Challenge.model_validate(vector["challenge"])
    transcript = signable_access_request(vector["challenge"])
    assert transcript.hex() == vector["signing_bytes_hex"]
    assert hashlib.sha256(transcript).hexdigest() == vector["signing_sha256"]
