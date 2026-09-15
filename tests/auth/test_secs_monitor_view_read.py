from __future__ import annotations

import base64
import hashlib
import inspect
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import devgraph.auth.secs_monitor_view_read as exact
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.secs_issue_create import SecSVerifierKey, SecSVerifierKeyRegistry
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Project
from devgraph.ops.secs_monitor_view_read_receiver import (
    REPLAY_STORE_NAME,
    DurableSecSMonitorViewReadReplayStore,
)
from devgraph.storage.memory import MemoryGraphStorage

NOW = 1_800_000_000
ORIGIN = "http://127.0.0.1:8080"
AUDIENCE = "devgraph://receiver-local"
POLICY_ID = "devgraph-monitor-view-local-v1"
POLICY_VERSION = 1
POLICY_DIGEST = "7c" * 32


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _header(value: dict[str, object]) -> str:
    return _b64url(exact._canonical_json(value))


def _receiver(
    *,
    now: int = NOW,
    replay_store: exact.SecSMonitorViewReadReplayStore | None = None,
) -> tuple[
    exact.SecSMonitorViewReadAdapter,
    MemoryGraphStorage,
    AuditLog,
    Ed25519PrivateKey,
    Ed25519PrivateKey,
]:
    secs_key = Ed25519PrivateKey.generate()
    page_key = Ed25519PrivateKey.generate()
    registry = SecSVerifierKeyRegistry(
        [
            SecSVerifierKey(
                key_id="secs-monitor-test-v1",
                public_key=secs_key.public_key().public_bytes_raw(),
            )
        ]
    )
    verifier = exact.SecSMonitorViewReadVerifier(
        exact.SecSMonitorViewReadVerifierConfig(
            audience=AUDIENCE,
            origin=ORIGIN,
            stable_issuer="secs:devgraph-receiver-local",
            policy_binding=exact.SecSMonitorViewReadPolicyBinding(
                policy_id=POLICY_ID,
                policy_version=POLICY_VERSION,
                policy_digest_sha256=POLICY_DIGEST,
            ),
            key_registry=registry,
            replay_cache=(
                replay_store
                if replay_store is not None
                else exact.SecSMonitorViewReadReplayCache(maximum_entries=32)
            ),
            clock=lambda: now,
        )
    )
    storage = MemoryGraphStorage()
    audit = AuditLog()
    return (
        exact.SecSMonitorViewReadAdapter(
            verifier=verifier,
            storage=storage,
            audit_log=audit,
        ),
        storage,
        audit,
        secs_key,
        page_key,
    )


def _session(
    secs_key: Ed25519PrivateKey,
    page_key: Ed25519PrivateKey,
    *,
    issued_at: int = NOW,
    expires_at: int = NOW + 300,
    overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], str]:
    unsigned: dict[str, object] = {
        "actor_id": f"pubkey:sha256:{'42' * 32}",
        "actor_signature_suite": "Ed25519",
        "audience": AUDIENCE,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "nonce": "AAECAwQFBgcICQoL",
        "operation": exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
        "origin": ORIGIN,
        "page_public_key_base64url": _b64url(
            page_key.public_key().public_bytes_raw()
        ),
        "receiver_policy_digest_sha256": POLICY_DIGEST,
        "receiver_policy_id": POLICY_ID,
        "receiver_policy_version": POLICY_VERSION,
        "schema": exact.DEVGRAPH_MONITOR_SESSION_SCHEMA_V1,
        "schema_version": 1,
        "secs_context_id": f"ctx:sha256:{'24' * 32}",
        "secs_verifier_key_id": "secs-monitor-test-v1",
        "secs_verifier_signature_suite": "Ed25519",
        "session_id": "AAECAwQFBgcICQoLDA0ODw",
        "wallet_presentation_digest_sha256": "19" * 32,
    }
    unsigned.update(overrides or {})
    signature = secs_key.sign(
        exact.DEVGRAPH_MONITOR_SESSION_SIGNATURE_DOMAIN_V1
        + exact._canonical_json(unsigned)
    )
    signed = {
        **unsigned,
        "secs_verifier_signature": _b64url(signature),
    }
    return signed, _header(signed)


def _proof(
    page_key: Ed25519PrivateKey,
    signed_session: dict[str, object],
    *,
    timestamp: int = NOW,
    nonce: str = "EBESExQVFhcYGRob",
    overrides: dict[str, object] | None = None,
) -> str:
    session_digest = hashlib.sha256(
        exact.DEVGRAPH_MONITOR_SESSION_DIGEST_DOMAIN_V1
        + exact._canonical_json(signed_session)
    ).hexdigest()
    unsigned: dict[str, object] = {
        "body_digest_sha256": hashlib.sha256(b"").hexdigest(),
        "method": "GET",
        "nonce": nonce,
        "operation": exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
        "origin": ORIGIN,
        "path_query": "/monitor/snapshot",
        "schema": exact.DEVGRAPH_MONITOR_REQUEST_PROOF_SCHEMA_V1,
        "schema_version": 1,
        "session_digest_sha256": session_digest,
        "session_id": signed_session["session_id"],
        "signature_suite": "Ed25519",
        "timestamp": timestamp,
    }
    unsigned.update(overrides or {})
    signature = page_key.sign(
        exact.DEVGRAPH_MONITOR_REQUEST_PROOF_SIGNATURE_DOMAIN_V1
        + exact._canonical_json(unsigned)
    )
    return _header({**unsigned, "signature": _b64url(signature)})


def _execute(
    adapter: exact.SecSMonitorViewReadAdapter,
    session_header: str,
    proof_header: str,
    *,
    method: str = "GET",
    path_query: str = "/monitor/snapshot",
    origin: str = ORIGIN,
) -> dict[str, object]:
    return adapter.execute(
        signed_session_header=session_header,
        request_proof_header=proof_header,
        method=method,
        path_query=path_query,
        origin=origin,
        body=b"",
    )


def test_exact_receiver_has_no_generic_credential_scope_route_or_mutation_seam() -> None:
    source = inspect.getsource(exact)
    assert set(inspect.signature(exact.SecSMonitorViewReadAdapter).parameters) == {
        "verifier",
        "storage",
        "audit_log",
    }
    assert set(inspect.signature(exact.SecSMonitorViewReadAdapter.execute).parameters) == {
        "self",
        "signed_session_header",
        "request_proof_header",
        "method",
        "path_query",
        "origin",
        "body",
    }
    for forbidden in (
        "AuthorityContext",
        "CredentialEnvelope",
        "EventOutbox",
        "LocalDevVerifier",
        "WriteSession",
    ):
        assert forbidden not in source


def test_valid_exact_pop_read_returns_snapshot_and_only_records_safe_read_audit() -> None:
    adapter, storage, audit, secs_key, page_key = _receiver()
    WorkObjectRepository(storage).create(Project(id="project-monitor", title="Monitor"))
    before_nodes = storage.query()
    before_edges = storage.list_edges()
    signed_session, session_header = _session(secs_key, page_key)
    proof_header = _proof(page_key, signed_session)

    snapshot = _execute(adapter, session_header, proof_header)

    assert snapshot["total_work"] == 1
    assert storage.query() == before_nodes
    assert storage.list_edges() == before_edges
    assert storage.query("EventReceipt") == []
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.operation == exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1
    assert record.category == "read"
    assert record.actor_id == f"pubkey:sha256:{'42' * 32}"
    assert record.safe_summary["origin"] == ORIGIN
    assert "signature" not in repr(record)


def test_replay_is_denied_before_snapshot_or_second_audit() -> None:
    adapter, storage, audit, secs_key, page_key = _receiver()
    signed_session, session_header = _session(secs_key, page_key)
    proof_header = _proof(page_key, signed_session)
    _execute(adapter, session_header, proof_header)
    before = storage.query(), storage.list_edges(), list(audit.records)

    with pytest.raises(exact.SecSMonitorViewReadDenied) as denied:
        _execute(adapter, session_header, proof_header)

    assert str(denied.value) == "monitor proof denied"
    assert (storage.query(), storage.list_edges(), audit.records) == before


def test_signed_session_remains_valid_after_receiver_restart_until_expiry() -> None:
    adapter, storage, audit, secs_key, page_key = _receiver(now=NOW + 1)
    signed_session, session_header = _session(
        secs_key,
        page_key,
        issued_at=NOW,
        expires_at=NOW + 300,
    )
    proof_header = _proof(page_key, signed_session, timestamp=NOW + 1)

    snapshot = _execute(adapter, session_header, proof_header)

    assert snapshot["total_work"] == 0
    assert len(audit.records) == 1


@pytest.mark.parametrize(
    ("session_overrides", "proof_overrides", "transport", "proof_key"),
    [
        ({"audience": "devgraph://wrong"}, {}, {}, "page"),
        ({"origin": "http://localhost:8080"}, {}, {}, "page"),
        ({"operation": "devgraph.read"}, {}, {}, "page"),
        ({"expires_at": NOW}, {}, {}, "page"),
        ({"issued_at": NOW + 1}, {}, {}, "page"),
        ({"secs_verifier_key_id": "unknown-key"}, {}, {}, "page"),
        ({}, {"origin": "http://localhost:8080"}, {}, "page"),
        ({}, {"path_query": "/monitor/snapshot?x=1"}, {}, "page"),
        ({}, {"method": "POST"}, {}, "page"),
        ({}, {"body_digest_sha256": "00" * 32}, {}, "page"),
        ({}, {"timestamp": NOW - 31}, {}, "page"),
        ({}, {}, {"origin": "http://localhost:8080"}, "page"),
        ({}, {}, {"path_query": "/monitor/snapshot?x=1"}, "page"),
        ({}, {}, {"method": "POST"}, "page"),
        ({}, {}, {}, "other"),
    ],
)
def test_exact_authority_and_request_mismatches_deny_without_side_effects(
    session_overrides: dict[str, object],
    proof_overrides: dict[str, object],
    transport: dict[str, str],
    proof_key: str,
) -> None:
    adapter, storage, audit, secs_key, page_key = _receiver()
    other_page_key = Ed25519PrivateKey.generate()
    signed_session, session_header = _session(
        secs_key,
        page_key,
        overrides=session_overrides,
    )
    proof_header = _proof(
        page_key if proof_key == "page" else other_page_key,
        signed_session,
        overrides=proof_overrides,
    )

    with pytest.raises(exact.SecSMonitorViewReadDenied) as denied:
        _execute(adapter, session_header, proof_header, **transport)

    assert str(denied.value) == "monitor proof denied"
    assert storage.query() == []
    assert storage.list_edges() == []
    assert audit.records == []


def test_preclaim_authority_denials_leave_durable_replay_bytes_identical(
    tmp_path: Path,
) -> None:
    replay_directory = tmp_path / "replay"
    replay_directory.mkdir(mode=0o700)
    replay_path = replay_directory / REPLAY_STORE_NAME
    replay_store = DurableSecSMonitorViewReadReplayStore(replay_path)
    adapter, storage, audit, secs_key, page_key = _receiver(
        replay_store=replay_store
    )
    other_secs_key = Ed25519PrivateKey.generate()
    other_page_key = Ed25519PrivateKey.generate()

    unknown_session, unknown_header = _session(
        secs_key,
        page_key,
        overrides={"secs_verifier_key_id": "unknown-key"},
    )
    bad_signature_session, _ = _session(secs_key, page_key)
    unsigned_bad_signature = dict(bad_signature_session)
    unsigned_bad_signature.pop("secs_verifier_signature")
    bad_signature_session["secs_verifier_signature"] = _b64url(
        other_secs_key.sign(
            exact.DEVGRAPH_MONITOR_SESSION_SIGNATURE_DOMAIN_V1
            + exact._canonical_json(unsigned_bad_signature)
        )
    )
    policy_session, policy_header = _session(
        secs_key,
        page_key,
        overrides={"receiver_policy_id": "wrong-policy"},
    )
    valid_session, valid_header = _session(secs_key, page_key)
    cases = [
        (unknown_header, _proof(page_key, unknown_session)),
        (
            _header(bad_signature_session),
            _proof(page_key, bad_signature_session),
        ),
        (policy_header, _proof(page_key, policy_session)),
        (valid_header, _proof(other_page_key, valid_session)),
    ]

    baseline = replay_path.read_bytes()
    for session_header, proof_header in cases:
        with pytest.raises(exact.SecSMonitorViewReadDenied):
            _execute(adapter, session_header, proof_header)
        assert replay_path.read_bytes() == baseline

    assert storage.query() == []
    assert storage.list_edges() == []
    assert audit.records == []


def test_session_signature_mismatch_unknown_fields_and_noncanonical_json_deny_safely() -> None:
    adapter, storage, audit, secs_key, page_key = _receiver()
    signed_session, _ = _session(secs_key, page_key)
    signed_session["origin"] = "http://localhost:8080"
    tampered_header = _header(signed_session)
    proof_header = _proof(page_key, signed_session)

    for bad_session in (
        tampered_header,
        _header({**signed_session, "unexpected": True}),
        _b64url(b'{"schema_version":1, "schema":"not-canonical"}'),
        "synthetic-secret-marker",
    ):
        with pytest.raises(exact.SecSMonitorViewReadDenied) as denied:
            _execute(adapter, bad_session, proof_header)
        assert str(denied.value) == "monitor proof denied"

    assert storage.query() == []
    assert audit.records == []
    assert "synthetic-secret-marker" not in repr(audit.records)


def test_replay_cache_is_bounded_and_fails_closed_at_capacity() -> None:
    cache = exact.SecSMonitorViewReadReplayCache(maximum_entries=1)
    cache.claim("11" * 32, "AAECAwQFBgcICQoL", now=NOW, expires_at=NOW + 10)
    with pytest.raises(exact.SecSMonitorViewReadDenied):
        cache.claim("22" * 32, "EBESExQVFhcYGRob", now=NOW, expires_at=NOW + 10)
    cache.claim("22" * 32, "EBESExQVFhcYGRob", now=NOW + 10, expires_at=NOW + 20)
