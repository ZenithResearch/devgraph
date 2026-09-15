"""Named authority bindings and transactional HTTP behavior; synthetic test keys only."""

import asyncio
import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services

from devgraph.api import create_app
from devgraph.auth import AuditLog
from devgraph.auth.secs_issue_create import (
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
    SecSIssueCreateVerifierConfig,
    SecSVerifierKey,
    SecSVerifierKeyRegistry,
)
from devgraph.auth.secs_work import (
    SIGNATURE_DOMAIN,
    SecSWorkAdapter,
    SecSWorkDenied,
    SecSWorkVerifier,
)
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.work_requests import WorkRequest

NOW = 1_800_000_000
KEY = "named-work-test-idempotency-0001"
SEED = Ed25519PrivateKey.from_private_bytes(bytes([41]) * 32)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def request(operation="create", kind="Issue", id="test-one", version=None, payload=None):
    return canonical(
        dict(
            schema="devgraph.work-request.v1",
            operation=operation,
            kind=kind,
            id=id,
            expected_version=version,
            payload=payload if payload is not None else {"id": id, "title": "test"},
        )
    )


def proof(raw, key=KEY, **overrides):
    parsed = WorkRequest.from_json(raw)
    value = dict(
        schema="secs-devgraph-work-authority.v1",
        schema_version=1,
        actor_id="pubkey:sha256:" + "a" * 64,
        actor_signature_suite="Ed25519",
        audience="devgraph://receiver-local",
        issued_at=NOW,
        expires_at=NOW + 60,
        session_id=b64(bytes(range(16))),
        nonce=b64(bytes(range(12))),
        operation=parsed.authority_operation,
        resources=list(parsed.resources),
        request_digest_sha256=parsed.digest,
        idempotency_key_digest_sha256=hashlib.sha256(key.encode()).hexdigest(),
        wallet_presentation_digest_sha256="b" * 64,
        receiver_policy_id="test-policy",
        receiver_policy_version=1,
        receiver_policy_digest_sha256="c" * 64,
        replay_scope="session:operation:nonce",
        secs_context_id="ctx:sha256:" + "d" * 64,
        secs_verifier_key_id="test-secs",
        secs_verifier_signature_suite="Ed25519",
    )
    value.update(overrides)
    value["secs_verifier_signature"] = b64(SEED.sign(SIGNATURE_DOMAIN + canonical(value)))
    return canonical(value)


def config():
    return SecSIssueCreateVerifierConfig(
        audience="devgraph://receiver-local",
        stable_issuer="secs:test-work",
        policy_registry=SecSIssueCreatePolicyRegistry(
            [
                SecSIssueCreatePolicyBinding(
                    policy_id="test-policy", policy_version=1, policy_digest_sha256="c" * 64
                )
            ]
        ),
        key_registry=SecSVerifierKeyRegistry(
            [SecSVerifierKey(key_id="test-secs", public_key=SEED.public_key().public_bytes_raw())]
        ),
        clock=lambda: NOW,
    )


def client_and_services(scopes=frozenset()):
    services = build_services(scopes)
    audit = AuditLog()
    adapter = SecSWorkAdapter(
        storage=services.storage, verifier=SecSWorkVerifier(config()), audit_log=audit
    )
    services = replace(services, named_work=adapter)
    return TestClient(create_app(services)), services, audit


def post(client, raw, key=KEY, projection=None, **kwargs):
    return client.post(
        "/work-operations/v1",
        content=raw,
        headers={
            "X-Devgraph-Work-Authority": b64(projection or proof(raw, key)),
            "Idempotency-Key": key,
            **kwargs,
        },
    )


@pytest.mark.parametrize("kind", ["Proposal", "Initiative", "Project", "Issue", "Task"])
def test_http_create_retry_fresh_session_and_stale_version(kind):
    client, services, audit = client_and_services()
    raw = request(kind=kind)
    first = post(client, raw)
    assert first.status_code == 201, first.text
    receipt = first.json()["receipt"]["receipt_id"]
    for projection in [proof(raw), proof(raw, session_id=b64(b"x" * 16), nonce=b64(b"y" * 12))]:
        again = post(client, raw, projection=projection)
        assert again.status_code == 200, again.text
        assert again.json()["work"] is None
        assert again.json()["receipt"]["receipt_id"] == receipt
    stale = post(
        client, request("patch", kind, version=2, payload={"title": "bad"}), key=KEY + "stale"
    )
    assert stale.status_code == 412
    assert len(services.storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(audit.records) == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_at": NOW},
        {"issued_at": NOW + 1},
        {"expires_at": NOW + 61},
        {"resources": ["Issue/wrong"]},
        {"request_digest_sha256": "0" * 64},
        {"idempotency_key_digest_sha256": "0" * 64},
        {"receiver_policy_digest_sha256": "0" * 64},
        {"receiver_policy_version": 2},
        {"secs_verifier_key_id": "missing"},
        {"schema": "secs-devgraph-authority.v1"},
        {"operation": "devgraph.work.archive.v1"},
        {"actor_signature_suite": "unknown"},
        {"extra": "hidden"},
        {"audience": "devgraph://wrong"},
    ],
)
def test_invalid_signed_authority_never_mutates(overrides):
    client, services, audit = client_and_services()
    raw = request()
    response = post(client, raw, projection=proof(raw, **overrides))
    assert response.status_code == 403, response.text
    assert services.storage.query() == []
    assert audit.records == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer fake-credential-api"},
        {"X-Devgraph-Work-Authority": "broken", "Idempotency-Key": KEY},
    ],
)
def test_http_denies_missing_or_wrong_authority(headers):
    client, services, _ = client_and_services()
    assert client.post("/work-operations/v1", content=request(), headers=headers).status_code == 403
    assert services.storage.query() == []


def test_real_asyncio_upload_deadline_returns_safe_denial(monkeypatch):
    client, services, audit = client_and_services()
    original = asyncio.wait_for

    async def expire_upload(awaitable, timeout):
        return await original(awaitable, timeout=0 if timeout == 10 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", expire_upload)
    response = post(client, request())
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Named Work authority denied"
    assert response.json()["detail"] == ""
    assert services.storage.query() == []
    assert audit.records == []


def test_body_substitution_and_cross_actor_retry_do_not_share_receipt():
    client, services, _ = client_and_services()
    raw = request()
    assert post(client, raw).status_code == 201
    assert post(client, request(id="other"), projection=proof(raw)).status_code == 403
    other = post(client, raw, projection=proof(raw, actor_id="pubkey:sha256:" + "e" * 64))
    assert other.status_code == 409
    assert len(services.storage.query(EVENT_RECEIPT_LABEL)) == 1


def test_verifier_clock_failure_is_redacted():
    def fail():
        raise RuntimeError("secret")

    verifier = SecSWorkVerifier(replace(config(), clock=fail))
    with pytest.raises(SecSWorkDenied, match="clock_unavailable"):
        verifier.verify(
            request_json=request(), projection_json=proof(request()), idempotency_key=KEY
        )


def test_shared_requests_remain_canonical():
    vectors = json.loads(
        (Path(__file__).parents[1] / "fixtures/named-work-v1/requests.json").read_text()
    )
    for vector in vectors:
        parsed = WorkRequest.from_json(vector["raw"].encode())
        assert parsed.canonical.decode() == vector["canonical"]
        assert parsed.digest == vector["digest"]
        assert list(parsed.resources) == vector["resources"]


@pytest.mark.parametrize("fixture", ["named-work-v1", "arena-v1"])
def test_real_native_secs_proofs_verify_in_python(fixture):
    vectors = json.loads(
        (Path(__file__).parents[1] / "fixtures" / fixture / "signed-vectors.json").read_text()
    )
    for vector in vectors:
        projection = vector["projection"]
        verifier = SecSWorkVerifier(
            SecSIssueCreateVerifierConfig(
                audience="devgraph://receiver-local",
                stable_issuer="secs:test-native",
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [
                        SecSIssueCreatePolicyBinding(
                            policy_id=projection["receiver_policy_id"],
                            policy_version=projection["receiver_policy_version"],
                            policy_digest_sha256=projection["receiver_policy_digest_sha256"],
                        )
                    ]
                ),
                key_registry=SecSVerifierKeyRegistry(
                    [
                        SecSVerifierKey(
                            key_id=projection["secs_verifier_key_id"],
                            public_key=base64.urlsafe_b64decode(vector["public_key"] + "="),
                        )
                    ]
                ),
                clock=lambda now=vector["now"]: now,
            )
        )
        verified = verifier.verify(
            request_json=canonical(vector["request"]),
            projection_json=canonical(projection),
            idempotency_key=vector["key"],
        )
        assert verified.request.resources == tuple(projection["resources"])
        assert verified.request.authority_operation == projection["operation"]


def test_proof_expiring_while_waiting_for_database_lock_has_no_effects():
    from contextlib import contextmanager

    from devgraph.storage.memory import MemoryGraphStorage

    current = [NOW]

    class DelayedLock(MemoryGraphStorage):
        @contextmanager
        def work_mutation_transaction(self):
            with self.transaction():
                current[0] = NOW + 60
                yield

    storage = DelayedLock()
    adapter = SecSWorkAdapter(
        storage=storage,
        audit_log=AuditLog(),
        verifier=SecSWorkVerifier(replace(config(), clock=lambda: current[0])),
    )
    with pytest.raises(SecSWorkDenied):
        adapter.execute(
            request_json=request(), projection_json=proof(request()), idempotency_key=KEY
        )
    assert storage.query() == []
