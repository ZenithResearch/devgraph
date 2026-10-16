import json
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.auth.test_secs_work import KEY, NOW, SEED, b64, canonical, proof, request

from devgraph.api import create_app
from devgraph.auth import AuditLog
from devgraph.auth.sdk_receiver_profile import receiver_profile_digest
from devgraph.auth.secs_work import SIGNATURE_DOMAIN, SecSWorkDenied
from devgraph.ops.named_work_receiver import BUNDLE, LocalNamedWorkReceiver
from devgraph.storage.memory import MemoryGraphStorage


def fixture(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path
    for part in BUNDLE.parts:
        path = path / part
        path.mkdir(mode=0o700)
    manifest = {
        "schema": "devgraph-secs-work-receiver.v1",
        "schema_version": 1,
        "audience": "devgraph://receiver-local",
        "stable_issuer": "secs:test-local",
        "policy_binding": {
            "policy_id": "test-policy",
            "policy_version": 1,
            "policy_digest_sha256": "c" * 64,
        },
    }
    registry = {
        "schema": "secs-public-verifier-key-registry.v1",
        "schema_version": 1,
        "keys": [
            {
                "key_id": "test-secs",
                "public_key_base64url": b64(SEED.public_key().public_bytes_raw()),
                "algorithm": "ed25519",
                "status": "active",
                "production_authority": True,
            }
        ],
    }
    for name, value in [("receiver.json", manifest), ("secs-public-key-registry.json", registry)]:
        (path / name).write_text(json.dumps(value))
        (path / name).chmod(0o600)
    storage = MemoryGraphStorage()
    receiver = LocalNamedWorkReceiver(
        data_root=tmp_path, storage=storage, audit_log=AuditLog(), clock=lambda: NOW
    )
    return receiver, storage, path, manifest, registry


@pytest.mark.parametrize("change", ["policy", "key", "permissions", "symlink", "missing"])
def test_current_owner_bundle_is_loaded_for_each_call(tmp_path, change):
    receiver, storage, path, manifest, registry = fixture(tmp_path)
    raw = request()
    receiver.execute(request_json=raw, projection_json=proof(raw), idempotency_key=KEY)
    before = (storage.query(), storage.list_edges())
    if change == "policy":
        manifest["policy_binding"]["policy_version"] = 2
        (path / "receiver.json").write_text(json.dumps(manifest))
    elif change == "key":
        registry["keys"][0]["status"] = "revoked"
        (path / "secs-public-key-registry.json").write_text(json.dumps(registry))
    elif change == "permissions":
        (path / "receiver.json").chmod(0o644)
    elif change == "missing":
        (path / "receiver.json").unlink()
    else:
        target = path / "original.json"
        (path / "receiver.json").rename(target)
        (path / "receiver.json").symlink_to(target)
    with pytest.raises(SecSWorkDenied):
        receiver.execute(request_json=raw, projection_json=proof(raw), idempotency_key=KEY)
    assert before == (storage.query(), storage.list_edges())


@pytest.mark.parametrize("change", ["missing", "policy", "key"])
def test_waiting_write_reloads_receiver_pins_after_acquiring_mutation_lock(tmp_path, change):
    receiver, storage, path, manifest, registry = fixture(tmp_path)
    original = storage.work_mutation_transaction

    @contextmanager
    def changed_while_waiting():
        with original():
            if change == "missing":
                (path / "receiver.json").unlink()
            elif change == "policy":
                manifest["policy_binding"]["policy_version"] = 2
                (path / "receiver.json").write_text(json.dumps(manifest))
            else:
                registry["keys"][0]["status"] = "revoked"
                (path / "secs-public-key-registry.json").write_text(json.dumps(registry))
            yield

    storage.work_mutation_transaction = changed_while_waiting
    raw = request(id="queued-after-revoke")
    with pytest.raises(SecSWorkDenied):
        receiver.execute(request_json=raw, projection_json=proof(raw), idempotency_key=KEY)
    assert storage.query() == []
    assert receiver.audit_log.records == []


@pytest.mark.parametrize("guarded", [False, True])
def test_queued_same_endpoint_issuer_change_denies_before_commit(tmp_path, guarded):
    """Same trust key still verifies, so final principal continuity must be checked."""
    receiver, storage, path, manifest, _ = fixture(tmp_path)
    original = storage.work_mutation_transaction

    @contextmanager
    def changed_while_waiting():
        with original():
            manifest["stable_issuer"] = "secs:changed-while-queued"
            (path / "receiver.json").write_text(json.dumps(manifest))
            yield

    storage.work_mutation_transaction = changed_while_waiting
    raw = request(id="queued-issuer-change")
    extra = {"receiver_profile": receiver_profile_digest(stable_issuer="secs:test-local")} \
        if guarded else {}
    reason = "sdk_receiver_profile_mismatch" if guarded else "named_work_admission_binding_changed"
    with pytest.raises(SecSWorkDenied, match=reason):
        receiver.execute(request_json=raw, projection_json=proof(raw),
                         idempotency_key=KEY, **extra)
    assert storage.query() == []
    assert receiver.audit_log.records == []


def test_guarded_http_loads_one_config_per_admission(tmp_path, monkeypatch):
    receiver, storage, _, _, _ = fixture(tmp_path)
    load = Mock(wraps=receiver._load_verifier)
    monkeypatch.setattr(receiver, "_load_verifier", load)
    services = replace(build_services(), named_work=receiver, storage=storage)
    client = TestClient(create_app(services))
    raw = request()
    response = client.post("/sdk/work-operations/v1", content=raw, headers={
        "X-Devgraph-Work-Authority": b64(proof(raw)),
        "Idempotency-Key": KEY,
        "X-Devgraph-Receiver-Profile": receiver_profile_digest(stable_issuer="secs:test-local"),
    })
    assert response.status_code == 201, response.text
    assert load.call_count == 2


def test_guarded_issuer_change_before_dispatch_never_enters_transaction(tmp_path, monkeypatch):
    receiver, storage, path, manifest, _ = fixture(tmp_path)
    old_profile = receiver_profile_digest(stable_issuer=manifest["stable_issuer"])
    manifest["stable_issuer"] = "secs:substituted-before-dispatch"
    (path / "receiver.json").write_text(json.dumps(manifest))
    transaction = Mock(side_effect=AssertionError("mutation storage must not be reached"))
    monkeypatch.setattr(storage, "work_mutation_transaction", transaction)
    raw = request()
    with pytest.raises(SecSWorkDenied, match="sdk_receiver_profile_mismatch"):
        receiver.execute(request_json=raw, projection_json=proof(raw),
                         idempotency_key=KEY, receiver_profile=old_profile)
    transaction.assert_not_called()
    assert storage.query() == []
    assert receiver.audit_log.records == []


def test_guarded_recovery_retains_profile_across_issuer_change_and_key_rotation(tmp_path):
    receiver, storage, path, manifest, registry = fixture(tmp_path)
    profile = receiver_profile_digest(stable_issuer=manifest["stable_issuer"])
    raw = request()
    _, first, _ = receiver.execute(request_json=raw, projection_json=proof(raw),
                                  idempotency_key=KEY, receiver_profile=profile)
    # An intervening edit is current state, not the original create result.
    patch = request(operation="patch", version=1, payload={"title": "intervening"})
    receiver.execute(request_json=patch, projection_json=proof(patch, KEY + "patch"),
                     idempotency_key=KEY + "patch", receiver_profile=profile)
    before = (storage.query(), storage.list_edges())
    manifest["stable_issuer"] = "secs:substituted-after-uncertain-commit"
    (path / "receiver.json").write_text(json.dumps(manifest))
    with pytest.raises(SecSWorkDenied, match="sdk_receiver_profile_mismatch"):
        receiver.execute(request_json=raw, projection_json=proof(raw),
                         idempotency_key=KEY, receiver_profile=profile)
    assert before == (storage.query(), storage.list_edges())

    # Restore the original issuer and install an explicitly authorized new key
    # and policy. Neither authority-rotation detail belongs in the identity pin.
    replacement_key = Ed25519PrivateKey.from_private_bytes(bytes([43]) * 32)
    manifest["stable_issuer"] = "secs:test-local"
    manifest["policy_binding"]["policy_version"] = 2
    registry["keys"] = [{
        **registry["keys"][0], "key_id": "test-rotated-secs",
        "public_key_base64url": b64(replacement_key.public_key().public_bytes_raw()),
    }]
    (path / "receiver.json").write_text(json.dumps(manifest))
    (path / "secs-public-key-registry.json").write_text(json.dumps(registry))
    renewed = json.loads(proof(raw, receiver_policy_version=2,
                               secs_verifier_key_id="test-rotated-secs",
                               session_id=b64(b"x" * 16), nonce=b64(b"y" * 12)))
    renewed.pop("secs_verifier_signature")
    renewed["secs_verifier_signature"] = b64(
        replacement_key.sign(SIGNATURE_DOMAIN + canonical(renewed))
    )
    work, duplicate, is_duplicate = receiver.execute(
        request_json=raw, projection_json=canonical(renewed), idempotency_key=KEY,
        receiver_profile=profile,
    )
    assert is_duplicate and work is None
    assert duplicate.id == first.id
    assert duplicate.correlation_id == first.correlation_id
    assert before == (storage.query(), storage.list_edges())


def test_admitted_transaction_can_finish_after_issuer_changes(tmp_path, monkeypatch):
    receiver, storage, path, manifest, _ = fixture(tmp_path)
    original = receiver._load_verifier
    decisions = []

    def verifier_that_changes_config_after_final_admission():
        verifier = original()
        decisions.append(verifier)
        if len(decisions) == 2:
            verify = verifier.verify

            def final_admission(**kwargs):
                result = verify(**kwargs)
                manifest["stable_issuer"] = "secs:changed-after-admission"
                (path / "receiver.json").write_text(json.dumps(manifest))
                return result

            verifier.verify = final_admission
        return verifier

    monkeypatch.setattr(receiver, "_load_verifier",
                        verifier_that_changes_config_after_final_admission)
    raw = request()
    work, _, duplicate = receiver.execute(
        request_json=raw, projection_json=proof(raw), idempotency_key=KEY,
        receiver_profile=receiver_profile_digest(stable_issuer="secs:test-local"),
    )
    assert work.id == "test-one" and not duplicate
    assert len(decisions) == 2
    assert receiver.audit_log.records[0].actor_id == "pubkey:sha256:" + "a" * 64
