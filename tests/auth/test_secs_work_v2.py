"""Generic credential authority migration; all signing keys are synthetic."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.auth.test_secs_work import KEY, NOW, SEED, b64, canonical, config, proof, request

from devgraph.api import create_app
from devgraph.auth import AuditLog
from devgraph.auth.secs_work import SecSWorkAdapter, SecSWorkDenied, SecSWorkVerifier
from devgraph.auth.secs_work_v2 import SIGNATURE_DOMAIN, SecSWorkV2Verifier
from devgraph.client import DevgraphHttpClient, DevgraphWorkV2Context
from devgraph.credential_requests import REQUEST_DOMAIN, credential_request_binding
from devgraph.storage.memory import MemoryGraphStorage


def proof_v2(raw, key=KEY, **overrides):
    value = json.loads(proof(raw, key))
    value.pop("secs_verifier_signature")
    value.pop("wallet_presentation_digest_sha256")
    binding = credential_request_binding(raw, key)
    value.update(
        schema="secs-devgraph-work-authority.v2",
        schema_version=2,
        credential_presentation_digest_sha256="e" * 64,
        credential_digest_sha256="f" * 64,
        credential_request_digest_sha256=binding.request_digest_sha256,
        disclosure_digest_sha256=binding.disclosure_digest_sha256,
        session_id="11" * 16,
        nonce="22" * 16,
        replay_scope="credential:operation:nonce",
    )
    value.update(overrides)
    value["secs_verifier_signature"] = b64(SEED.sign(SIGNATURE_DOMAIN + canonical(value)))
    return canonical(value)


def services_v2():
    services = build_services(frozenset())
    audit = AuditLog()
    return replace(
        services,
        named_work_v2=SecSWorkAdapter(
            storage=services.storage, verifier=SecSWorkV2Verifier(config()), audit_log=audit
        ),
    ), audit


def test_explicit_v2_client_replays_receipt_without_downgrade():
    services, audit = services_v2()
    client = DevgraphHttpClient(
        transport=TestClient(create_app(services)), base_url="http://testserver", timeout=10
    )
    raw = request()
    context = DevgraphWorkV2Context(projection_json=proof_v2(raw))
    first = client.execute_named_work(context, request_json=raw, idempotency_key=KEY)
    duplicate = client.execute_named_work(context, request_json=raw, idempotency_key=KEY)
    assert duplicate.receipt.receipt_id == first.receipt.receipt_id
    assert duplicate.receipt.duplicate
    assert len(services.storage.query("Issue")) == 1
    assert len(audit.records) == 2
    assert audit.records[0].safe_summary["credential_digest_sha256"] == "[REDACTED]"


@pytest.mark.parametrize(
    "field",
    [
        "request_digest_sha256",
        "idempotency_key_digest_sha256",
        "credential_request_digest_sha256",
        "disclosure_digest_sha256",
        "receiver_policy_digest_sha256",
    ],
)
def test_signed_binding_substitution_fails_without_mutation(field):
    services, audit = services_v2()
    with pytest.raises(SecSWorkDenied):
        services.named_work_v2.execute(
            request_json=request(),
            idempotency_key=KEY,
            projection_json=proof_v2(request(), **{field: "0" * 64}),
        )
    assert services.storage.query() == []
    assert audit.records == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 1},
        {"schema_version": True},
        {"schema": "secs-devgraph-work-authority.v1"},
        {"extra": "no"},
        {"wallet_presentation_digest_sha256": "0" * 64},
        {"session_id": b64(bytes(16))},
        {"nonce": b64(bytes(12))},
        {"nonce": "A" * 32},
        {"replay_scope": "session:operation:nonce"},
        {"issued_at": NOW + 1},
        {"expires_at": NOW},
        {"expires_at": NOW + 61},
        {"resources": ["Issue/substitution"]},
        {"credential_digest_sha256": "G" * 64},
    ],
)
def test_closed_v2_fields_fail(overrides):
    with pytest.raises(SecSWorkDenied):
        SecSWorkV2Verifier(config()).verify(
            request_json=request(),
            idempotency_key=KEY,
            projection_json=proof_v2(request(), **overrides),
        )


def test_v1_v2_proofs_never_cross_decoders():
    with pytest.raises(SecSWorkDenied):
        SecSWorkVerifier(config()).verify(
            request_json=request(), idempotency_key=KEY, projection_json=proof_v2(request())
        )
    with pytest.raises(SecSWorkDenied):
        SecSWorkV2Verifier(config()).verify(
            request_json=request(), idempotency_key=KEY, projection_json=proof(request())
        )


def test_disclosure_and_wrapper_bind_complete_request_and_idempotency():
    raw = request(payload={"id": "test-one", "title": "changed", "description": "é" * 1000})
    binding = credential_request_binding(raw, KEY)
    wrapper = json.loads(binding.request_bytes[len(REQUEST_DOMAIN) :])
    assert wrapper["idempotency_key_digest_sha256"] == hashlib.sha256(KEY.encode()).hexdigest()
    displayed = "".join(part.split(": ", 1)[1] for part in binding.disclosure["statements"][4:])
    assert json.loads(displayed) == wrapper["request"]
    assert all(len(part.encode()) <= 512 for part in binding.disclosure["statements"])
    changed = credential_request_binding(raw, KEY + "-new")
    assert changed.request_digest_sha256 != binding.request_digest_sha256
    assert changed.disclosure_digest_sha256 != binding.disclosure_digest_sha256


def test_disclosure_controls_rejected_instead_of_hidden_or_truncated():
    raw = request(payload={"id": "test-one", "title": "spoof\u202etitle"})
    with pytest.raises(ValueError, match="unsupported_credential_disclosure"):
        credential_request_binding(raw, KEY)


def test_v2_verification_repeats_inside_mutation_lock():
    clock = [NOW]
    storage, audit = MemoryGraphStorage(), AuditLog()
    verifier = SecSWorkV2Verifier(replace(config(), clock=lambda: clock[0]))
    adapter = SecSWorkAdapter(storage=storage, verifier=verifier, audit_log=audit)
    original = storage.work_mutation_transaction

    @contextmanager
    def delayed():
        with original():
            clock[0] = NOW + 60
            yield

    storage.work_mutation_transaction = delayed
    with pytest.raises(SecSWorkDenied):
        adapter.execute(
            request_json=request(), idempotency_key=KEY, projection_json=proof_v2(request())
        )
    assert storage.query() == []
    assert audit.records == []


@pytest.mark.parametrize("name", ["authority-vectors.json", "progress-authority-vectors.json"])
def test_rust_issued_work_and_arena_v2_vectors_and_disclosures(name):
    from pathlib import Path

    from devgraph.auth.secs_issue_create import (
        SecSIssueCreatePolicyBinding,
        SecSIssueCreatePolicyRegistry,
        SecSIssueCreateVerifierConfig,
        SecSVerifierKey,
        SecSVerifierKeyRegistry,
    )

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/credential-v2" / name).read_text()
    )
    for vector in fixture["vectors"]:
        projection = vector["projection"]
        raw = canonical(vector["request"])
        binding = credential_request_binding(raw, vector["idempotency_key"])
        assert binding.disclosure == vector["presentation_request"]["disclosure"]
        assert binding.request_digest_sha256 == projection["credential_request_digest_sha256"]
        expected_policy = hashlib.sha256(
            b"secs-devgraph-work-policy.v1\0" + canonical(vector["policy"])
        ).hexdigest()
        assert expected_policy == projection["receiver_policy_digest_sha256"]
        verifier = SecSWorkV2Verifier(
            SecSIssueCreateVerifierConfig(
                audience=projection["audience"],
                stable_issuer="secs:test-native-v2",
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [
                        SecSIssueCreatePolicyBinding(
                            policy_id=vector["policy"]["policy_id"],
                            policy_version=vector["policy"]["policy_version"],
                            policy_digest_sha256=expected_policy,
                        )
                    ]
                ),
                key_registry=SecSVerifierKeyRegistry(
                    [
                        SecSVerifierKey(
                            key_id=projection["secs_verifier_key_id"],
                            public_key=bytes.fromhex(fixture["issuer_public_key"]),
                        )
                    ]
                ),
                clock=lambda now=vector["now"]: now + 1,
            )
        )
        result = verifier.verify(
            request_json=raw,
            projection_json=canonical(projection),
            idempotency_key=vector["idempotency_key"],
        )
        assert result.request.digest == projection["request_digest_sha256"]
