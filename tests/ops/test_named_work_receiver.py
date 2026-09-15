import json
from contextlib import contextmanager

import pytest
from tests.auth.test_secs_work import KEY, NOW, SEED, b64, proof, request

from devgraph.auth import AuditLog
from devgraph.auth.secs_work import SecSWorkDenied
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
