"""Real secS binary, disposable issuer/holder, no live installation access."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_http import fixture, sign

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.signed_read import SecsReadAuthority, SignedReadEnrollment

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def setup(tmp_path, arena=False):
    binary = os.environ.get("SECS_READ_AUTHORITY_TEST_BINARY")
    if not binary:
        pytest.skip("matched secS binary required")
    owner, public, membership, revoked, policies, old = fixture(tmp_path)
    if arena:
        from devgraph.auth.native_read import EnrollmentPolicy

        raw = policies[0].model_dump(mode="json", by_alias=True, exclude_defaults=True)
        raw["grants"][0].pop("work_ids")
        raw["grants"][0]["arena_ids"] = ["zenith"]
        policies[0] = EnrollmentPolicy.model_validate(raw)
    issuer = Ed25519PrivateKey.generate()
    key = tmp_path / "issuer.key"
    key.write_text(issuer.private_bytes_raw().hex())
    key.chmod(0o600)
    policy_path = tmp_path / "authority.json"
    authority_policy = SecsReadAuthority.policy_for(
        policies[0], key_id="fixture-read", public_key=issuer.public_key().public_bytes_raw().hex()
    )
    policy_path.write_text(json.dumps(authority_policy))
    policy_path.chmod(0o600)
    authority = SecsReadAuthority(binary=Path(binary), policy_file=policy_path, issuer_key_file=key)
    service = SignedReadEnrollment(
        path=tmp_path / "signed.sqlite",
        policy=lambda: policies[0],
        membership_check=old.membership_check,
        authority=authority,
    )
    challenge = service.challenge(public, "a" * 32, now=NOW)
    result = service.enroll(challenge, membership, sign(owner, challenge), now=NOW)
    return service, owner, result, policies, revoked


def request(owner, credential, nonce="11" * 16, operation="devgraph.work.list"):
    call = {
        "operation": operation,
        "resource": "https://work.zenith-research.ca/devgraph",
        "parameters": {"kind": "Task", "limit": 1},
    }
    proof = {
        "schema": "castalia.read-presentation.v1",
        "credential_digest": hashlib.sha256(canonical_json(credential)).hexdigest(),
        "call_digest": hashlib.sha256(canonical_json(call)).hexdigest(),
        "receiver": credential["claims"]["receiver"],
        "nonce": nonce,
        "issued_at": int(NOW.timestamp()),
    }
    proof["signature"] = owner.sign(
        b"castalia.read-presentation.v1/signature\0" + canonical_json(proof)
    ).hex()
    return {"credential": credential, "call": call, "proof": proof}


def test_signed_credential_holder_replay_and_revocation(tmp_path):
    service, owner, result, _, _ = setup(tmp_path)
    credential = result["credential"]
    assert result["schema"] == "devgraph.native-read-result.v2"
    assert credential["claims"]["schema"] == "castalia.read-credential.v1"
    req = request(owner, credential)
    context = service.verify_request(**req, now=NOW)
    assert context.read_grants[0].work_ids == ("Task/allowed-1", "Task/allowed-2")
    with pytest.raises(UnauthenticatedError, match="replay"):
        service.verify_request(**req, now=NOW)
    with pytest.raises(UnauthenticatedError):
        service.verify_request(
            **request(Ed25519PrivateKey.generate(), credential, "22" * 16), now=NOW
        )
    with pytest.raises(UnauthenticatedError):
        service.verify_request(
            **request(owner, credential, "33" * 16, "devgraph.work.delete"), now=NOW
        )
    service.revoke(credential["claims"]["credential_id"])
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential, "44" * 16), now=NOW)
    with pytest.raises(UnauthenticatedError):
        service.verify(json.dumps(credential), audience="devgraph", now=NOW)


def test_arena_filtering_before_pagination_and_archive_is_separate(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.api.test_app_scaffold import build_services

    from devgraph.api.signed_read import create_signed_read_app
    from devgraph.auth.enforcement import AuditLog
    from devgraph.model.repository import WorkObjectRepository
    from devgraph.model.work import Project, Task

    monkeypatch.setattr("devgraph.auth.signed_read._seconds", lambda _: int(NOW.timestamp()))
    service, owner, result, _, _ = setup(tmp_path, arena=True)
    storage = build_services(frozenset()).storage
    repository = WorkObjectRepository(storage)
    for item in [
        Project(id="root", title="Root"),
        Task(id="a-outside", title="Outside"),
        Task(id="y-inside", title="Inside"),
        Task(id="z-inside", title="Inside 2"),
    ]:
        repository.create(item)
    storage.create_node("Arena", "zenith", {"kind": "Arena"})
    storage.create_edge("Arena", "zenith", "CONTAINS_WORK", "Project", "root")
    for identifier in ["y-inside", "z-inside"]:
        storage.create_edge("Project", "root", "HAS_CHILD", "Task", identifier)
    http = TestClient(
        create_signed_read_app(storage=storage, enrollment=service, audit_log=AuditLog())
    )
    credential = result["credential"]
    first = http.post("/devgraph/native-read/v2/requests", json=request(owner, credential))
    assert first.status_code == 200, first.text
    assert [i["id"] for i in first.json()["items"]] == ["y-inside"]

    def changed(parameters, nonce):
        body = request(owner, credential, nonce)
        body["call"]["parameters"] = parameters
        p = body["proof"]
        p["call_digest"] = hashlib.sha256(canonical_json(body["call"])).hexdigest()
        p.pop("signature")
        p["signature"] = owner.sign(
            b"castalia.read-presentation.v1/signature\0" + canonical_json(p)
        ).hex()
        return http.post("/devgraph/native-read/v2/requests", json=body)

    second = changed({"kind": "Task", "limit": 1, "after_id": "y-inside"}, "22" * 16)
    assert [i["id"] for i in second.json()["items"]] == ["z-inside"]
    assert changed({"kind": "Task", "include_archived": True}, "33" * 16).status_code == 404
    assert changed({"kind": "Task", "limit": 101}, "44" * 16).status_code == 401
    assert http.get("/devgraph/work/Task").status_code == 404


def test_changed_policy_and_membership_revocation_fail_closed(tmp_path):
    service, owner, result, policies, revoked = setup(tmp_path)
    credential = result["credential"]
    # Real membership validator, with current operator revocation registry.
    with service._database() as db:
        membership = json.loads(db.execute("SELECT proof FROM credentials").fetchone()[0])
    revoked.add(membership["membershipId"])
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW)
    revoked.clear()
    from datetime import timedelta

    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW + timedelta(hours=2))
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW - timedelta(seconds=1))
    policies[0] = policies[0].model_copy(update={"lifetime_seconds": 1800})
    with pytest.raises(UnauthenticatedError):
        service.verify_request(**request(owner, credential), now=NOW)
