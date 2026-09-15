from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Lock, Thread
from uuid import uuid4

import neo4j
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import devgraph.auth.secs_issue_create as exact
from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.auth.secs_issue_create import (
    DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
    SecSIssueCreateAdapter,
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
    SecSIssueCreateVerifier,
    SecSIssueCreateVerifierConfig,
    SecSVerifierKey,
    SecSVerifierKeyRegistry,
)
from devgraph.events.outbox import (
    EMITTED_EVENT,
    EVENT_RECEIPT_LABEL,
    EventOutbox,
    IdempotencyScopeConflict,
)
from devgraph.ops.migrate import (
    MigrationJournal,
    apply_migrations,
    load_manifest,
    migration_status,
)
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

IMAGE = "neo4j:5.26-community"
WORKER_IMAGE_DIGEST = "sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf"
WORKER_IMAGE = f"python:3.12-slim@{WORKER_IMAGE_DIGEST}"
EXPECTED_DRIVER_VERSION = "6.2.0"
EXPECTED_DIGEST = "sha256:4bae36aff76271e27fd6a6ed0835413f86a284cd179cfb1cb7d188f5f7533aca"
ROOT = Path(__file__).parents[2]
SECS_FIXTURES = ROOT / "tests/fixtures/secs_devgraph_issue_create_v1"
SECS_EXPECTED_NOW = 1_800_000_000


def _proof_authority(*, session_id: str, correlation_id: str) -> AuthorityContext:
    return AuthorityContext(
        CredentialEnvelope(
            actor_id="actor-live-proof",
            session_id=session_id,
            correlation_id=correlation_id,
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            issuer="issuer-live-proof",
            audience="devgraph-live-proof",
        )
    )


def _exercise_atomic_receipt_claims(storage: Neo4jGraphStorage) -> dict[str, object]:
    evidence: dict[str, object] = {}

    same_scope_barrier = Barrier(2)
    same_scope_results = []
    same_scope_errors: list[str] = []
    same_scope_lock = Lock()

    def same_scope_receipt_id() -> str:
        same_scope_barrier.wait(timeout=10)
        return f"receipt-live-same-{uuid4().hex}"

    same_scope_outbox = EventOutbox(
        storage,
        receipt_id_factory=same_scope_receipt_id,
    )

    def same_scope_worker(index: int) -> None:
        try:
            result = same_scope_outbox.record_mutation_with_receipt(
                authority=_proof_authority(
                    session_id=f"session-live-same-{index}",
                    correlation_id=f"correlation-live-same-{index}",
                ),
                operation="create_issue",
                subject_label="Issue",
                subject_id="issue-live-same",
                idempotency_key="live-same-scope-key",
                summary={"proof": "same-scope"},
                mutation=lambda tx: tx.create_node(
                    "Issue",
                    "issue-live-same",
                    {"title": "Live same-scope winner"},
                ),
            )
            with same_scope_lock:
                same_scope_results.append(result)
        except BaseException as exc:
            with same_scope_lock:
                same_scope_errors.append(type(exc).__name__)

    same_scope_threads = [Thread(target=same_scope_worker, args=(index,)) for index in (1, 2)]
    for thread in same_scope_threads:
        thread.start()
    for thread in same_scope_threads:
        thread.join(timeout=30)

    evidence["claim_same_scope_threads_stopped"] = all(
        not thread.is_alive() for thread in same_scope_threads
    )
    evidence["claim_same_scope_errors"] = same_scope_errors
    evidence["claim_same_scope_mutation_count"] = sum(
        result is not None for result, _receipt in same_scope_results
    )
    evidence["claim_same_scope_receipt_ids"] = len(
        {receipt.id for _result, receipt in same_scope_results}
    )
    evidence["claim_same_scope_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["claim_same_scope_issue_count"] = len(storage.query("Issue"))
    evidence["claim_same_scope_edge_count"] = len(
        storage.list_edges(EMITTED_EVENT)
    )

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")

    different_scope_barrier = Barrier(2)
    different_scope_outcomes: list[str] = []
    different_scope_lock = Lock()

    def different_scope_receipt_id() -> str:
        different_scope_barrier.wait(timeout=10)
        return f"receipt-live-different-{uuid4().hex}"

    different_scope_outbox = EventOutbox(
        storage,
        receipt_id_factory=different_scope_receipt_id,
    )

    def different_scope_worker(subject_id: str) -> None:
        try:
            different_scope_outbox.record_mutation_with_receipt(
                authority=_proof_authority(
                    session_id=f"session-{subject_id}",
                    correlation_id=f"correlation-{subject_id}",
                ),
                operation="create_issue",
                subject_label="Issue",
                subject_id=subject_id,
                idempotency_key="live-different-scope-key",
                summary={"proof": "different-scope"},
                mutation=lambda tx: tx.create_node(
                    "Issue",
                    subject_id,
                    {"title": f"Live candidate {subject_id}"},
                ),
            )
            outcome = "created"
        except IdempotencyScopeConflict:
            outcome = "conflict"
        except BaseException as exc:
            outcome = type(exc).__name__
        with different_scope_lock:
            different_scope_outcomes.append(outcome)

    different_scope_threads = [
        Thread(target=different_scope_worker, args=(subject_id,))
        for subject_id in ("issue-live-one", "issue-live-two")
    ]
    for thread in different_scope_threads:
        thread.start()
    for thread in different_scope_threads:
        thread.join(timeout=30)

    evidence["claim_different_scope_threads_stopped"] = all(
        not thread.is_alive() for thread in different_scope_threads
    )
    evidence["claim_different_scope_outcomes"] = sorted(different_scope_outcomes)
    evidence["claim_different_scope_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["claim_different_scope_issue_count"] = len(storage.query("Issue"))
    evidence["claim_different_scope_edge_count"] = len(
        storage.list_edges(EMITTED_EVENT)
    )

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")

    rollback_outbox = EventOutbox(
        storage,
        receipt_id_factory=lambda: f"receipt-live-rollback-{uuid4().hex}",
    )
    try:
        rollback_outbox.record_mutation_with_receipt(
            authority=_proof_authority(
                session_id="session-live-rollback-one",
                correlation_id="correlation-live-rollback-one",
            ),
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-live-rollback",
            idempotency_key="live-rollback-key",
            summary={"proof": "rollback"},
            mutation=lambda _tx: (_ for _ in ()).throw(
                RuntimeError("synthetic rollback proof")
            ),
        )
    except RuntimeError:
        rollback_failed = True
    else:
        rollback_failed = False
    rollback_result, _rollback_receipt = rollback_outbox.record_mutation_with_receipt(
        authority=_proof_authority(
            session_id="session-live-rollback-two",
            correlation_id="correlation-live-rollback-two",
        ),
        operation="create_issue",
        subject_label="Issue",
        subject_id="issue-live-rollback",
        idempotency_key="live-rollback-key",
        summary={"proof": "rollback-retry"},
        mutation=lambda tx: tx.create_node(
            "Issue",
            "issue-live-rollback",
            {"title": "Live rollback retry"},
        ),
    )
    evidence["claim_rollback_failed"] = rollback_failed
    evidence["claim_rollback_retry_created"] = rollback_result is not None
    evidence["claim_rollback_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["claim_rollback_issue_count"] = len(storage.query("Issue"))
    evidence["claim_rollback_edge_count"] = len(storage.list_edges(EMITTED_EVENT))

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")
    return evidence


def _live_secs_contract(
    request: dict[str, object],
    idempotency_key: str,
    signing_key: Ed25519PrivateKey,
    *,
    key_id: str,
    session_id: str,
) -> tuple[bytes, bytes, SecSVerifierKey]:
    request_json = exact._canonical_json(request)
    issue, canonical_request = exact._parse_issue_create_request(request_json)
    projection = json.loads((SECS_FIXTURES / "unsigned-projection.json").read_bytes())
    projection.update(
        {
            "idempotency_key_digest_sha256": hashlib.sha256(
                idempotency_key.encode("ascii")
            ).hexdigest(),
            "request_digest_sha256": hashlib.sha256(
                exact.DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 + canonical_request
            ).hexdigest(),
            "resource": f"Issue/{issue.id}",
            "secs_verifier_key_id": key_id,
            "session_id": session_id,
        }
    )
    signature = signing_key.sign(
        exact.DEVGRAPH_ISSUE_CREATE_SIGNATURE_DOMAIN_V1
        + exact._canonical_json(projection)
    )
    projection["secs_verifier_signature"] = (
        base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    return (
        request_json,
        exact._canonical_json(projection),
        SecSVerifierKey(
            key_id=key_id,
            public_key=signing_key.public_key().public_bytes_raw(),
        ),
    )


def _exercise_exact_secs_issue_create(storage: Neo4jGraphStorage) -> dict[str, object]:
    policy_json = json.loads(
        (SECS_FIXTURES / "receiver-policy-binding.json").read_bytes()
    )
    policy = SecSIssueCreatePolicyBinding(
        policy_id=policy_json["policy_id"],
        policy_version=policy_json["policy_version"],
        policy_digest_sha256=policy_json["policy_digest_sha256"],
    )
    fixture_registry = SecSVerifierKeyRegistry.from_json(
        (SECS_FIXTURES / "secs-public-key-registry.json").read_bytes()
    )
    fixture_key = fixture_registry.require_production_key(
        "secs-devgraph-authority-v1",
        now=SECS_EXPECTED_NOW,
    )
    signing_key = Ed25519PrivateKey.generate()
    generated_key_id = "live-secs-generated-key"
    generated_request, generated_projection, generated_key = _live_secs_contract(
        {
            "id": "issue-live-secs-audit",
            "kind": "Issue",
            "title": "Live exact audit title",
            "description": "Live exact audit description",
        },
        "live-secs-audit-rollback-key",
        signing_key,
        key_id=generated_key_id,
        session_id="AQIDBAUGBwgJCgsMDQ4PEA",
    )

    def verifier() -> SecSIssueCreateVerifier:
        return SecSIssueCreateVerifier(
            SecSIssueCreateVerifierConfig(
                audience="devgraph://receiver-local",
                stable_issuer="secs:devgraph-receiver-local",
                policy_registry=SecSIssueCreatePolicyRegistry([policy]),
                key_registry=SecSVerifierKeyRegistry(
                    [fixture_key, generated_key]
                ),
                clock=lambda: SECS_EXPECTED_NOW,
            )
        )

    audit = AuditLog()
    retry_barrier = Barrier(2)

    def concurrent_receipt_id() -> str:
        retry_barrier.wait(timeout=10)
        return f"receipt-live-secs-{uuid4().hex}"

    adapter = SecSIssueCreateAdapter(
        verifier=verifier(),
        storage=storage,
        audit_log=audit,
        receipt_id_factory=concurrent_receipt_id,
    )
    execute = {
        "request_json": (SECS_FIXTURES / "request.json").read_bytes(),
        "idempotency_key": (
            (SECS_FIXTURES / "idempotency-key.txt")
            .read_text(encoding="ascii")
            .rstrip("\n")
        ),
        "signed_projection_json": (
            SECS_FIXTURES / "signed-projection.json"
        ).read_bytes(),
    }
    retry_results = []
    retry_errors: list[str] = []
    retry_lock = Lock()

    def retry_worker() -> None:
        try:
            result = adapter.execute(**execute)
            with retry_lock:
                retry_results.append(result)
        except BaseException as exc:
            with retry_lock:
                retry_errors.append(type(exc).__name__)

    retry_threads = [Thread(target=retry_worker) for _ in range(2)]
    for thread in retry_threads:
        thread.start()
    for thread in retry_threads:
        thread.join(timeout=30)

    sequential_retry = adapter.execute(**execute)
    receipts = storage.query(EVENT_RECEIPT_LABEL)
    issues = storage.query("Issue")
    edges = storage.list_edges(EMITTED_EVENT)
    winning_receipt = receipts[0]
    evidence_projection = repr(winning_receipt.properties) + repr(audit.records)
    raw_key = str(execute["idempotency_key"])
    fixture_signature = json.loads(execute["signed_projection_json"])[
        "secs_verifier_signature"
    ]
    evidence = {
        "secs_exact_retry_threads_stopped": all(
            not thread.is_alive() for thread in retry_threads
        ),
        "secs_exact_retry_errors": retry_errors,
        "secs_exact_fresh_count": sum(
            result.issue is not None for result in retry_results
        ),
        "secs_exact_duplicate_count": sum(
            result.duplicate for result in retry_results
        ),
        "secs_exact_receipt_ids": len(
            {result.receipt.id for result in retry_results}
        ),
        "secs_exact_sequential_retry_duplicate": sequential_retry.duplicate,
        "secs_exact_operation": sequential_retry.receipt.operation,
        "secs_exact_issue_count": len(issues),
        "secs_exact_receipt_count": len(receipts),
        "secs_exact_edge_count": len(edges),
        "secs_exact_audit_count": len(audit.records),
        "secs_exact_audit_duplicates": [
            record.safe_summary["duplicate"] for record in audit.records
        ],
        "secs_exact_request_digest": winning_receipt.properties.get(
            "request_digest_sha256"
        ),
        "secs_exact_raw_key_digest": winning_receipt.properties.get(
            "idempotency_key_digest_sha256"
        ),
        "secs_exact_evidence_redacted": all(
            secret not in evidence_projection
            for secret in (
                raw_key,
                "Golden issue",
                "Live exact audit description",
                fixture_signature,
            )
        ),
    }

    changed_request, changed_projection, _ = _live_secs_contract(
        {
            "id": "issue-golden",
            "kind": "Issue",
            "title": "Changed golden issue",
        },
        raw_key,
        signing_key,
        key_id=generated_key_id,
        session_id="AgMEBQYHCAkKCwwNDg8QEQ",
    )
    before_changed = (
        storage.query(),
        storage.list_edges(),
        list(audit.records),
    )
    try:
        adapter.execute(
            request_json=changed_request,
            idempotency_key=raw_key,
            signed_projection_json=changed_projection,
        )
    except IdempotencyScopeConflict:
        changed_conflicted = True
    else:
        changed_conflicted = False
    evidence["secs_exact_changed_request_conflicted"] = changed_conflicted
    evidence["secs_exact_changed_request_zero_mutation"] = before_changed == (
        storage.query(),
        storage.list_edges(),
        list(audit.records),
    )

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")

    conflict_barrier = Barrier(2)

    def conflict_receipt_id() -> str:
        conflict_barrier.wait(timeout=10)
        return f"receipt-live-secs-conflict-{uuid4().hex}"

    conflict_audit = AuditLog()
    conflict_adapter = SecSIssueCreateAdapter(
        verifier=verifier(),
        storage=storage,
        audit_log=conflict_audit,
        receipt_id_factory=conflict_receipt_id,
    )
    conflict_key = "live-secs-conflict-key"
    conflict_contracts = [
        _live_secs_contract(
            {
                "id": "issue-live-secs-conflict",
                "kind": "Issue",
                "title": title,
            },
            conflict_key,
            signing_key,
            key_id=generated_key_id,
            session_id=session_id,
        )[:2]
        for title, session_id in (
            ("Conflict candidate one", "AwQFBgcICQoLDA0ODxAREg"),
            ("Conflict candidate two", "BAUGBwgJCgsMDQ4PEBESEw"),
        )
    ]
    conflict_outcomes: list[str] = []
    conflict_lock = Lock()

    def conflict_worker(contract: tuple[bytes, bytes]) -> None:
        try:
            conflict_adapter.execute(
                request_json=contract[0],
                idempotency_key=conflict_key,
                signed_projection_json=contract[1],
            )
            outcome = "created"
        except IdempotencyScopeConflict:
            outcome = "conflict"
        except BaseException as exc:
            outcome = type(exc).__name__
        with conflict_lock:
            conflict_outcomes.append(outcome)

    conflict_threads = [
        Thread(target=conflict_worker, args=(contract,))
        for contract in conflict_contracts
    ]
    for thread in conflict_threads:
        thread.start()
    for thread in conflict_threads:
        thread.join(timeout=30)
    evidence["secs_exact_conflict_threads_stopped"] = all(
        not thread.is_alive() for thread in conflict_threads
    )
    evidence["secs_exact_conflict_outcomes"] = sorted(conflict_outcomes)
    evidence["secs_exact_conflict_issue_count"] = len(storage.query("Issue"))
    evidence["secs_exact_conflict_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["secs_exact_conflict_edge_count"] = len(
        storage.list_edges(EMITTED_EVENT)
    )
    evidence["secs_exact_conflict_audit_count"] = len(conflict_audit.records)

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")

    class FailingAuditLog(AuditLog):
        def record(self, **kwargs: object):  # type: ignore[no-untyped-def]
            raise RuntimeError("injected live audit failure")

    failing_adapter = SecSIssueCreateAdapter(
        verifier=verifier(),
        storage=storage,
        audit_log=FailingAuditLog(),
        receipt_id_factory=lambda: f"receipt-live-secs-audit-{uuid4().hex}",
    )
    try:
        failing_adapter.execute(
            request_json=generated_request,
            idempotency_key="live-secs-audit-rollback-key",
            signed_projection_json=generated_projection,
        )
    except RuntimeError:
        audit_failed = True
    else:
        audit_failed = False
    evidence["secs_exact_audit_failed"] = audit_failed
    evidence["secs_exact_audit_failure_issue_count"] = len(storage.query("Issue"))
    evidence["secs_exact_audit_failure_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["secs_exact_audit_failure_edge_count"] = len(
        storage.list_edges(EMITTED_EVENT)
    )

    healthy_audit = AuditLog()
    healthy_adapter = SecSIssueCreateAdapter(
        verifier=verifier(),
        storage=storage,
        audit_log=healthy_audit,
        receipt_id_factory=lambda: f"receipt-live-secs-retry-{uuid4().hex}",
    )
    recovered = healthy_adapter.execute(
        request_json=generated_request,
        idempotency_key="live-secs-audit-rollback-key",
        signed_projection_json=generated_projection,
    )
    evidence["secs_exact_audit_retry_created"] = recovered.issue is not None
    evidence["secs_exact_audit_retry_issue_count"] = len(storage.query("Issue"))
    evidence["secs_exact_audit_retry_receipt_count"] = len(
        storage.query(EVENT_RECEIPT_LABEL)
    )
    evidence["secs_exact_audit_retry_edge_count"] = len(
        storage.list_edges(EMITTED_EVENT)
    )
    evidence["secs_exact_audit_retry_audit_count"] = len(healthy_audit.records)

    storage._run_graph("MATCH (n:EventReceipt) DETACH DELETE n")
    storage._run_graph("MATCH (n:Issue) DETACH DELETE n")
    return evidence


def _docker(*args: str, timeout: int = 180) -> str:
    try:
        completed = subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"docker command failed: {exc.stderr.strip()}") from exc
    return completed.stdout.strip()


def _wait_for_bolt(container: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            completed = subprocess.run(
                ["docker", "exec", container, "cypher-shell", "--non-interactive", "RETURN 1"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            time.sleep(1)
            continue
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("disposable Neo4j did not become ready")


def _exercise_migrations(uri: str) -> dict[str, object]:
    storage = Neo4jGraphStorage(Neo4jConfig(uri, "", ""))
    try:
        nested_receipt = {
            "state": "pending",
            "attempts": 0,
            "kind": "delivery",
            "redacted_summary": {"safe": 1, "nested": {"value": True}},
        }
        created_receipt = storage.create_node(
            "EventReceipt", "event-receipt-live", nested_receipt
        )
        try:
            storage.create_node(
                "EventReceipt", "event-receipt-live", {"state": "duplicate"}
            )
        except KeyError:
            duplicate_create_rejected = True
        else:
            duplicate_create_rejected = False
        loaded_receipt = storage.get_node("EventReceipt", "event-receipt-live")
        updated_receipt_properties = {
            **nested_receipt,
            "state": "retry-scheduled",
            "attempts": 1,
            "redacted_summary": {"safe": 2, "nested": {"value": False}},
        }
        updated_receipt = storage.update_node(
            "EventReceipt", "event-receipt-live", updated_receipt_properties
        )
        archived_receipt = storage.archive_node(
            "EventReceipt", "event-receipt-live"
        )
        generic_nested_round_trip = bool(
            created_receipt.properties == nested_receipt
            and loaded_receipt is not None
            and loaded_receipt.properties == nested_receipt
            and updated_receipt.properties == updated_receipt_properties
            and archived_receipt.archived
            and archived_receipt.properties == updated_receipt_properties
        )

        legacy_generic_properties = {
            "message": '__devgraph_generic_json_v1__:{"legacy":true}',
            "state": "pending",
        }
        storage._run_graph(
            "CREATE (n:EventReceipt {id: $node_id, archived: false}) "
            "SET n += $properties",
            node_id="event-receipt-legacy",
            properties=legacy_generic_properties,
        )
        converted_legacy = storage.update_node(
            "EventReceipt",
            "event-receipt-legacy",
            {"state": "retry-scheduled"},
        )
        archived_legacy = storage.archive_node(
            "EventReceipt",
            "event-receipt-legacy",
            {"state": "failed"},
        )
        legacy_generic_conversion_round_trip = bool(
            converted_legacy.properties
            == {**legacy_generic_properties, "state": "retry-scheduled"}
            and archived_legacy.archived
            and archived_legacy.properties
            == {**legacy_generic_properties, "state": "failed"}
        )
        first_edge = storage.create_edge(
            "EventReceipt",
            "event-receipt-live",
            "EMITTED_EVENT",
            "EventReceipt",
            "event-receipt-legacy",
            {"required": True},
        )
        duplicate_edge = storage.create_edge(
            "EventReceipt",
            "event-receipt-live",
            "EMITTED_EVENT",
            "EventReceipt",
            "event-receipt-legacy",
            {"required": True},
        )
        identical_edge_deduplicated = bool(
            first_edge == duplicate_edge
            and storage.list_edges("EMITTED_EVENT") == [first_edge]
        )

        manifest = load_manifest(ROOT / "migrations/manifest.json")
        acquire_barrier = Barrier(2)
        acquired_barrier = Barrier(2)
        bootstrap_barrier = Barrier(2)
        bootstrap_lock = Lock()

        class ContendedStore(Neo4jMigrationStore):
            def execute_bootstrap(self, statement: str) -> None:
                bootstrap_barrier.wait()
                with bootstrap_lock:
                    super().execute_bootstrap(statement)

            def acquire_owner(self, attempt_id: str) -> bool:
                acquire_barrier.wait()
                acquired = super().acquire_owner(attempt_id)
                acquired_barrier.wait()
                return acquired

        results = []

        def run(attempt_id: str) -> None:
            results.append(
                apply_migrations(manifest, ContendedStore(storage), attempt_id=attempt_id)
            )

        threads = [Thread(target=run, args=(attempt,)) for attempt in ("attempt-a", "attempt-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert all(not thread.is_alive() for thread in threads)

        store = Neo4jMigrationStore(storage)
        store._run("MATCH (n:EventReceipt) DETACH DELETE n")
        atomic_claim_evidence = _exercise_atomic_receipt_claims(storage)
        exact_secs_evidence = _exercise_exact_secs_issue_create(storage)
        reasons = sorted(result.reason for result in results)
        evidence = {
            **atomic_claim_evidence,
            **exact_secs_evidence,
            "duplicate_create_rejected": duplicate_create_rejected,
            "generic_nested_round_trip": generic_nested_round_trip,
            "identical_edge_deduplicated": identical_edge_deduplicated,
            "legacy_generic_conversion_round_trip": (
                legacy_generic_conversion_round_trip
            ),
            "neo4j_driver_version": neo4j.__version__,
            "bootstrap": store.inspect_bootstrap_constraint(),
            "bootstrap_count": int(store.inspect_bootstrap_constraint() is not None),
            "application_constraint_count": sum(
                1
                for migration in manifest.migrations
                if store.inspect_schema_object(migration.name)
            ),
            "journal_applied_count": sum(
                1
                for record in store.inspect_journal()
                if isinstance(record, MigrationJournal) and record.state == "applied"
            ),
            "concurrent_winners": reasons.count("clean"),
            "concurrent_losers": reasons.count("migration_lock_busy"),
            "reasons": reasons,
        }

        def reset_application_state() -> None:
            for migration in manifest.migrations:
                store._run(f"DROP CONSTRAINT {migration.name} IF EXISTS")
            store._run("MATCH (m:DevgraphMigration) WHERE m.version >= 1 DETACH DELETE m")
            store._run(
                "MATCH (m:DevgraphMigration {version: 0}) REMOVE m.owner_attempt_id "
                "SET m.state = 'clean', m.runner_schema_version = 1"
            )

        def create_legacy_task(
            node_id: str, status: str, *, kind: str | None = "Task"
        ) -> None:
            properties = {
                "id": node_id,
                "title": node_id,
                "description": "",
                "status": status,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "version": 1,
                "priority": 0,
                "artifact_ids": [],
                "external_link_ids": [],
            }
            if kind is not None:
                properties["kind"] = kind
            store._run(
                "CREATE (n:Task) SET n = $properties",
                properties=properties,
            )

        reset_application_state()
        create_legacy_task("task-active", "draft")
        create_legacy_task("task-archived", "archived")
        legacy_applied = apply_migrations(manifest, store, attempt_id="legacy-valid")
        evidence["legacy_valid_reason"] = legacy_applied.reason
        evidence["legacy_backfill"] = store._run(
            "MATCH (n:Task) RETURN n.id AS id, n.status AS status, "
            "n.archived AS archived ORDER BY n.id"
        )
        evidence["legacy_second_run_reason"] = apply_migrations(
            manifest, store, attempt_id="legacy-noop"
        ).reason
        store._run("MATCH (n:Task) DETACH DELETE n")

        reset_application_state()
        create_legacy_task("task-missing-kind", "draft", kind=None)
        missing_kind = apply_migrations(
            manifest, store, attempt_id="legacy-missing-kind"
        )
        evidence["legacy_missing_kind_reason"] = missing_kind.reason
        evidence["legacy_missing_kind_row"] = store._run(
            "MATCH (n:Task {id: 'task-missing-kind'}) "
            "RETURN n.archived AS archived, n.kind AS kind"
        )
        evidence["legacy_missing_kind_v23_count"] = len(
            [record for record in store.inspect_journal() if record.version == 23]
        )
        store._run("MATCH (n:Task) DETACH DELETE n")

        reset_application_state()
        create_legacy_task("task-malformed", "draft", kind="Issue")
        malformed = apply_migrations(manifest, store, attempt_id="legacy-malformed")
        evidence["legacy_malformed_reason"] = malformed.reason
        evidence["legacy_malformed_row"] = store._run(
            "MATCH (n:Task {id: 'task-malformed'}) "
            "RETURN n.archived AS archived, n.kind AS kind"
        )
        evidence["legacy_malformed_v23_count"] = len(
            [record for record in store.inspect_journal() if record.version == 23]
        )
        store._run("MATCH (n:Task) DETACH DELETE n")
        recovered_transactional = apply_migrations(
            manifest,
            store,
            attempt_id="legacy-recovered",
            recovery_owner_attempt_id="legacy-malformed",
        )
        evidence["legacy_recovered_reason"] = recovered_transactional.reason
        evidence["legacy_recovered_v23_count"] = len(
            [record for record in store.inspect_journal() if record.version == 23]
        )

        store._run(
            "MATCH (m:DevgraphMigration {version: 0}) "
            "SET m.owner_attempt_id = 'post-marker-crash', m.state = 'owned'"
        )
        post_marker_recovered = apply_migrations(
            manifest,
            store,
            attempt_id="post-marker-recovered",
            recovery_owner_attempt_id="post-marker-crash",
        )
        evidence["post_marker_recovered_reason"] = post_marker_recovered.reason
        evidence["post_marker_v23_count"] = len(
            [record for record in store.inspect_journal() if record.version == 23]
        )

        reset_application_state()

        class FailingDdlStore(Neo4jMigrationStore):
            def execute_ddl(self, payload: bytes) -> None:
                raise RuntimeError("injected redacted DDL failure")

        recoverable = apply_migrations(manifest, FailingDdlStore(storage), attempt_id="ddl-fail")
        evidence["ddl_absent_reason"] = recoverable.reason
        evidence["ddl_absent_object"] = store.inspect_schema_object(manifest.migrations[0].name)
        evidence["ddl_absent_journal_state"] = store.inspect_journal()[0].state

        reset_application_state()

        class MarkerFailStore(Neo4jMigrationStore):
            failed = False

            def compare_and_set_journal(self, before, after, attempt_id: str) -> bool:
                if getattr(after, "state", None) == "ddl_observed" and not self.failed:
                    self.failed = True
                    return False
                return super().compare_and_set_journal(before, after, attempt_id)

        marker_failed = apply_migrations(manifest, MarkerFailStore(storage), attempt_id="marker")
        evidence["marker_failed_reason"] = marker_failed.reason
        evidence["marker_failed_state"] = store.inspect_journal()[0].state
        reconciled = apply_migrations(
            manifest,
            store,
            attempt_id="fresh-marker",
            recovery_owner_attempt_id="marker",
        )
        evidence["marker_reconciled_reason"] = reconciled.reason
        evidence["marker_reconciled_state"] = store.inspect_journal()[0].state

        reset_application_state()
        assert store.acquire_owner("cas-owner")
        expected = MigrationJournal.started(manifest.migrations[0], "cas-owner")
        assert store.compare_and_set_journal(None, expected, "cas-owner")
        wrong = replace(expected, checksum="0" * 64)
        cas_before = store.inspect_journal()
        cas_changed = store.compare_and_set_journal(
            wrong, replace(wrong, state="ddl_observed"), "cas-owner"
        )
        evidence["wrong_identity_cas_changed"] = cas_changed
        evidence["wrong_identity_zero_mutation"] = store.inspect_journal() == cas_before

        store._run(
            "MATCH (m:DevgraphMigration {version: 0}) REMOVE m.owner_attempt_id "
            "SET m.state = 'clean'"
        )
        abandoned_before = store.inspect_journal()
        abandoned = apply_migrations(manifest, store, attempt_id="new-owner")
        evidence["abandoned_owner_reason"] = abandoned.reason
        evidence["abandoned_owner_zero_mutation"] = store.inspect_journal() == abandoned_before

        store._run("MATCH (m:DevgraphMigration {version: 0}) REMOVE m.state")
        evidence["malformed_owner_status_reason"] = migration_status(manifest, store).reason
        return evidence
    finally:
        storage.close()


def _run_worker(network: str, container: str) -> dict[str, object]:
    command = (
        "python -m pip install --quiet 'cryptography==50.0.1' "
        "'neo4j==6.2.0' 'pytest==8.3.5' && "
        "PYTHONPATH=/workspace/src python /workspace/tests/integration/test_neo4j_migrations.py "
        "--worker bolt://neo4j-proof:7687"
    )
    output = _docker(
        "run",
        "--rm",
        "--network",
        network,
        "--volume",
        f"{ROOT}:/workspace:ro",
        "--workdir",
        "/workspace",
        WORKER_IMAGE,
        "sh",
        "-c",
        command,
        timeout=240,
    )
    return json.loads(output.splitlines()[-1])


def run_live_migration_proof() -> dict[str, object]:
    suffix = uuid4().hex[:12]
    network = f"devgraph-migrations-{suffix}"
    name = f"devgraph-migrations-{suffix}"
    container = ""
    result: dict[str, object] = {}
    _docker("network", "create", network)
    try:
        container = _docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "neo4j-proof",
            "--env",
            "NEO4J_AUTH=none",
            IMAGE,
        )
        _wait_for_bolt(container)
        published_ports = _docker(
            "inspect", container, "--format", "{{json .NetworkSettings.Ports}}"
        )
        evidence = _run_worker(network, container)
        digest = _docker("image", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}")
        worker_digest = _docker(
            "image", "inspect", WORKER_IMAGE, "--format", "{{index .RepoDigests 0}}"
        )
        version = _docker("exec", container, "neo4j", "--version").split()[-1]
        result = {
            **evidence,
            "image_digest": digest.rsplit("@", 1)[-1],
            "worker_image_digest": worker_digest.rsplit("@", 1)[-1],
            "server_version": version,
            "published_ports": published_ports,
        }
    finally:
        rm_container = (
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if container
            else None
        )
        rm_network = subprocess.run(
            ["docker", "network", "rm", network],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_absent = not container or subprocess.run(
            ["docker", "inspect", container], check=False, capture_output=True, timeout=30
        ).returncode != 0
        network_absent = subprocess.run(
            ["docker", "network", "inspect", network],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode != 0
        result["cleanup"] = bool(
            (rm_container is None or rm_container.returncode == 0)
            and rm_network.returncode == 0
            and container_absent
            and network_absent
        )
    return result


@pytest.mark.skipif(
    os.environ.get("DEVGRAPH_TEST_NEO4J") != "1", reason="explicit disposable Neo4j gate"
)
def test_pinned_neo4j_bootstrap_journal_and_concurrency() -> None:
    evidence = run_live_migration_proof()
    assert evidence["image_digest"] == EXPECTED_DIGEST
    assert evidence["worker_image_digest"] == WORKER_IMAGE_DIGEST
    assert evidence["neo4j_driver_version"] == EXPECTED_DRIVER_VERSION
    assert evidence["server_version"] == "5.26.28"
    assert evidence["duplicate_create_rejected"] is True
    assert evidence["generic_nested_round_trip"] is True
    assert evidence["identical_edge_deduplicated"] is True
    assert evidence["legacy_generic_conversion_round_trip"] is True
    assert all(binding is None for binding in json.loads(str(evidence["published_ports"])).values())
    assert evidence["bootstrap_count"] == 1
    assert evidence["bootstrap"] == [
        "devgraph_migration_version_unique",
        "UNIQUENESS",
        "DevgraphMigration",
        ["version"],
        "CREATE CONSTRAINT devgraph_migration_version_unique IF NOT EXISTS "
        "FOR (m:DevgraphMigration) REQUIRE m.version IS UNIQUE",
    ]
    assert evidence["reasons"] == ["clean", "migration_lock_busy"]
    assert evidence["application_constraint_count"] == 23
    assert evidence["journal_applied_count"] == 24
    assert evidence["legacy_valid_reason"] == "clean"
    assert evidence["legacy_backfill"] == [
        {"id": "task-active", "status": "draft", "archived": False},
        {"id": "task-archived", "status": "archived", "archived": True},
    ]
    assert evidence["legacy_second_run_reason"] == "clean"
    assert evidence["legacy_missing_kind_reason"] == "operator_hold_unexpected_store_failure"
    assert evidence["legacy_missing_kind_row"] == [{"archived": None, "kind": None}]
    assert evidence["legacy_missing_kind_v23_count"] == 0
    assert evidence["legacy_malformed_reason"] == "operator_hold_unexpected_store_failure"
    assert evidence["legacy_malformed_row"] == [{"archived": None, "kind": "Issue"}]
    assert evidence["legacy_malformed_v23_count"] == 0
    assert evidence["legacy_recovered_reason"] == "clean"
    assert evidence["legacy_recovered_v23_count"] == 1
    assert evidence["post_marker_recovered_reason"] == "clean"
    assert evidence["post_marker_v23_count"] == 1
    assert evidence["concurrent_winners"] == 1
    assert evidence["concurrent_losers"] == 1
    assert evidence["claim_same_scope_threads_stopped"] is True
    assert evidence["claim_same_scope_errors"] == []
    assert evidence["claim_same_scope_mutation_count"] == 1
    assert evidence["claim_same_scope_receipt_ids"] == 1
    assert evidence["claim_same_scope_receipt_count"] == 1
    assert evidence["claim_same_scope_issue_count"] == 1
    assert evidence["claim_same_scope_edge_count"] == 1
    assert evidence["claim_different_scope_threads_stopped"] is True
    assert evidence["claim_different_scope_outcomes"] == ["conflict", "created"]
    assert evidence["claim_different_scope_receipt_count"] == 1
    assert evidence["claim_different_scope_issue_count"] == 1
    assert evidence["claim_different_scope_edge_count"] == 1
    assert evidence["claim_rollback_failed"] is True
    assert evidence["claim_rollback_retry_created"] is True
    assert evidence["claim_rollback_receipt_count"] == 1
    assert evidence["claim_rollback_issue_count"] == 1
    assert evidence["claim_rollback_edge_count"] == 1
    assert evidence["secs_exact_retry_threads_stopped"] is True
    assert evidence["secs_exact_retry_errors"] == []
    assert evidence["secs_exact_fresh_count"] == 1
    assert evidence["secs_exact_duplicate_count"] == 1
    assert evidence["secs_exact_receipt_ids"] == 1
    assert evidence["secs_exact_sequential_retry_duplicate"] is True
    assert evidence["secs_exact_operation"] == DEVGRAPH_ISSUE_CREATE_OPERATION_V1
    assert evidence["secs_exact_issue_count"] == 1
    assert evidence["secs_exact_receipt_count"] == 1
    assert evidence["secs_exact_edge_count"] == 1
    assert evidence["secs_exact_audit_count"] == 3
    assert sorted(evidence["secs_exact_audit_duplicates"]) == [False, True, True]
    assert evidence["secs_exact_request_digest"] == (
        "dd1f3ed1bdd7171956e51f81414bb4d790d44ae51339884c3667583793be0706"
    )
    assert evidence["secs_exact_raw_key_digest"] == (
        "b2d81561ff6835c04849321c6c40ada8638e454f16694da2dabfaf4a32a754ea"
    )
    assert evidence["secs_exact_evidence_redacted"] is True
    assert evidence["secs_exact_changed_request_conflicted"] is True
    assert evidence["secs_exact_changed_request_zero_mutation"] is True
    assert evidence["secs_exact_conflict_threads_stopped"] is True
    assert evidence["secs_exact_conflict_outcomes"] == ["conflict", "created"]
    assert evidence["secs_exact_conflict_issue_count"] == 1
    assert evidence["secs_exact_conflict_receipt_count"] == 1
    assert evidence["secs_exact_conflict_edge_count"] == 1
    assert evidence["secs_exact_conflict_audit_count"] == 1
    assert evidence["secs_exact_audit_failed"] is True
    assert evidence["secs_exact_audit_failure_issue_count"] == 0
    assert evidence["secs_exact_audit_failure_receipt_count"] == 0
    assert evidence["secs_exact_audit_failure_edge_count"] == 0
    assert evidence["secs_exact_audit_retry_created"] is True
    assert evidence["secs_exact_audit_retry_issue_count"] == 1
    assert evidence["secs_exact_audit_retry_receipt_count"] == 1
    assert evidence["secs_exact_audit_retry_edge_count"] == 1
    assert evidence["secs_exact_audit_retry_audit_count"] == 1
    assert evidence["ddl_absent_reason"] == "recoverable_ddl_not_applied"
    assert evidence["ddl_absent_object"] is False
    assert evidence["ddl_absent_journal_state"] == "recoverable_ddl_not_applied"
    assert evidence["marker_failed_reason"] == "operator_hold_journal_transition"
    assert evidence["marker_failed_state"] == "ddl_started"
    assert evidence["marker_reconciled_reason"] == "clean"
    assert evidence["marker_reconciled_state"] == "applied"
    assert evidence["wrong_identity_cas_changed"] is False
    assert evidence["wrong_identity_zero_mutation"] is True
    assert evidence["abandoned_owner_reason"] == "operator_hold_owner_mismatch"
    assert evidence["abandoned_owner_zero_mutation"] is True
    assert evidence["malformed_owner_status_reason"] == "operator_hold_owner_inspection"
    assert evidence["cleanup"] is True


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    print(json.dumps(_exercise_migrations(sys.argv[2]), sort_keys=True))
