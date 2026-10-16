"""Disposable issuer -> possession enrollment -> actual guarded Work reader."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services

from devgraph.api.native_read import create_native_read_app
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_membership import ZenithMembershipValidator
from devgraph.auth.native_read import EnrollmentPolicy, NativeReadEnrollment, signable_challenge
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task


def fixture(tmp_path):
    owner, issuer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    public = owner.public_key().public_bytes_raw().hex()
    root = {
        "issuerId": "fixture",
        "keyId": "disposable",
        "signatureSuite": "Ed25519",
        "publicKey": issuer.public_key().public_bytes_raw().hex(),
    }
    trust = {
        "v3": {
            "schema": "castalia.zenith-membership-trust-policy.v1",
            "version": 1,
            "roots": [root],
        }
    }
    proof = {
        "schema": "castalia.zenith-membership-credential.v3",
        "version": 3,
        "membershipId": hashlib.sha256(
            b"castalia/zenith-membership-id/v3\0" + bytes.fromhex(public)
        ).hexdigest(),
        "ownerPublicKey": public,
        "status": "active",
        "issuerId": "fixture",
        "issuerKeyId": "disposable",
        "signatureSuite": "Ed25519",
    }
    message = b"castalia/zenith-membership-credential/v3\0" + (3).to_bytes(8, "little")
    message += bytes.fromhex(proof["membershipId"]) + bytes.fromhex(public)
    for text in ("active", "fixture", "disposable", "ed25519"):
        message += len(text.encode()).to_bytes(4, "little") + text.encode()
    proof["issuerSignature"] = base64.urlsafe_b64encode(issuer.sign(message)).rstrip(b"=").decode()
    revoked = set()
    membership = ZenithMembershipValidator(lambda: trust, lambda mid: mid in revoked)
    assert membership(proof, public)
    policy = [
        EnrollmentPolicy.model_validate(
            {
                "schema": "devgraph.native-read-policy.v1",
                "issuer": "fixture",
                "receiver": "https://example.test",
                "lifetime_seconds": 3600,
                "grants": [
                    {
                        "scope": "devgraph.work.read",
                        "operations": ["list", "get"],
                        "relationship_types": ["parent"],
                        "work_kinds": ["Task"],
                        "work_ids": ["Task/allowed-1", "Task/allowed-2"],
                        "include_archived": False,
                    }
                ],
            }
        )
    ]
    service = NativeReadEnrollment(
        path=tmp_path / "native.sqlite", policy=lambda: policy[0], membership_check=membership
    )
    return owner, public, proof, revoked, policy, service


def sign(owner, challenge):
    return base64.urlsafe_b64encode(owner.sign(signable_challenge(challenge))).rstrip(b"=").decode()


def test_real_membership_guarded_reads_and_no_write_route(tmp_path):
    owner, public, proof, revoked, _, service = fixture(tmp_path)
    services = build_services(frozenset())
    repo = WorkObjectRepository(services.storage)
    for identifier in ("allowed-1", "allowed-2", "hidden"):
        repo.create(Task(id=identifier, title=identifier))
    client = TestClient(
        create_native_read_app(storage=services.storage, enrollment=service, audit_log=AuditLog())
    )
    challenge = client.post(
        "/devgraph/native-read/challenges",
        json={"subject_public_key": public, "installation_id": "a" * 32},
    ).json()
    body = {"challenge": challenge, "proof": proof, "signature": sign(owner, challenge)}
    result = client.post("/devgraph/native-read/enrollments", json=body)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    credential = result.json()["credential"]
    headers = {"Authorization": "Bearer " + credential}
    page = client.get("/devgraph/work/Task?limit=1", headers=headers)
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert [x["id"] for x in page.json()["items"]] == ["allowed-1"]
    page = client.get("/devgraph/work/Task?limit=1&after_id=allowed-1", headers=headers)
    assert [x["id"] for x in page.json()["items"]] == ["allowed-2"]
    assert client.get("/devgraph/work/Task/hidden", headers=headers).status_code == 404
    assert client.get("/devgraph/work/Project", headers=headers).status_code == 404
    assert client.post("/devgraph/todo-operations/v2", json={}, headers=headers).status_code == 404
    assert client.post("/devgraph/native-read/enrollments", json=body).status_code == 401
    revoked.add(proof["membershipId"])
    assert client.get("/devgraph/work/Task", headers=headers).status_code == 401
    assert credential not in repr(services.storage)


def test_expired_challenge_policy_changes_and_renewal_ceiling(tmp_path):
    owner, public, proof, _, policy, service = fixture(tmp_path)
    now = datetime.now(timezone.utc)
    challenge = service.challenge(public, "b" * 32, now=now)
    with pytest.raises(UnauthenticatedError):
        service.enroll(challenge, proof, sign(owner, challenge), now=now + timedelta(seconds=121))
    fresh = service.challenge(public, "b" * 32, now=now)
    result = service.enroll(fresh, proof, sign(owner, fresh), now=now)
    value = policy[0].model_dump(mode="json", by_alias=True, exclude_defaults=True)
    value["grants"][0]["work_ids"] = ["Task/allowed-1", "Task/allowed-2", "Task/hidden"]
    policy[0] = EnrollmentPolicy.model_validate(value)
    with pytest.raises(UnauthenticatedError):
        service.verify(result["credential"], audience="devgraph", now=now)
    wider = service.challenge(public, "b" * 32, now=now)
    with pytest.raises(UnauthenticatedError, match="grant_amplification"):
        service.enroll(wider, proof, sign(owner, wider), now=now)
