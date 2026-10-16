"""Guarded receiver contract; synthetic Ed25519 authorities and isolated storage."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.auth.test_secs_work import (
    KEY,
    b64,
    client_and_services,
    config,
    post,
    proof,
    request,
)

from devgraph.auth import AuditLog
from devgraph.auth.sdk_receiver_profile import (
    RECEIVER_PROFILE_HEADER,
    receiver_profile_canonical,
    receiver_profile_digest,
)
from devgraph.auth.secs_work import SecSWorkAdapter, SecSWorkDenied, SecSWorkVerifier
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.work_requests import WorkRequest

PROFILE = receiver_profile_digest(stable_issuer="secs:test-work")
SDK_ROUTE = "/sdk/work-operations/v1"


def sdk_headers(raw, profile=PROFILE):
    return [
        ("X-Devgraph-Work-Authority", b64(proof(raw))),
        ("Idempotency-Key", KEY),
        (RECEIVER_PROFILE_HEADER, profile),
    ]


def test_receiver_profile_shared_vectors():
    path = Path(__file__).parents[1] / "fixtures/sdk-receiver-profile-v1.json"
    for vector in json.loads(path.read_text())["vectors"]:
        parameters = {
            key: vector["profile"][key] for key in ("origin", "audience", "stable_issuer")
        }
        assert receiver_profile_canonical(**parameters).decode() == vector["canonical"]
        assert receiver_profile_digest(**parameters) == vector["digest_sha256"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"origin": "http://localhost:8080"},
        {"origin": "http://127.0.0.1:9999"},
        {"origin": "http://127.0.0.1:8080/"},
        {"audience": "devgraph://other"},
        {"stable_issuer": ""},
        {"stable_issuer": "secs:test\n"},
        {"stable_issuer": "secs:é"},
        {"stable_issuer": None},
    ],
)
def test_receiver_profile_rejects_unsupported_configuration(overrides):
    with pytest.raises(ValueError, match="invalid SDK receiver profile"):
        receiver_profile_digest(**{"stable_issuer": "secs:test-work", **overrides})


def test_guarded_create_and_renewed_retry_keep_legacy_result_shape():
    client, services, audit = client_and_services()
    raw = request()
    first = client.post(SDK_ROUTE, content=raw, headers=sdk_headers(raw))
    assert first.status_code == 201, first.text
    renewed = proof(raw, session_id=b64(b"x" * 16), nonce=b64(b"y" * 12))
    headers = sdk_headers(raw)
    headers[0] = (headers[0][0], b64(renewed))
    again = client.post(SDK_ROUTE, content=raw, headers=headers)
    assert again.status_code == 200
    assert again.json()["work"] is None
    assert again.json()["receipt"] == {**first.json()["receipt"], "duplicate": True}
    assert len(services.storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(audit.records) == 2


@pytest.mark.parametrize(
    "profile",
    [
        "",
        "A" * 64,
        "0" * 63,
        "0" * 65,
        "g" * 64,
        "0" * 64,
        PROFILE + "," + PROFILE,
        " " + PROFILE,
        PROFILE + " ",
    ],
)
def test_malformed_or_mismatched_profile_has_no_effects(profile):
    client, services, audit = client_and_services()
    raw = request()
    response = client.post(SDK_ROUTE, content=raw, headers=sdk_headers(raw, profile))
    assert response.status_code == 403
    assert response.json()["detail"] == ""
    assert services.storage.query() == []
    assert audit.records == []


@pytest.mark.parametrize(
    "extra",
    [
        [(RECEIVER_PROFILE_HEADER.lower(), PROFILE)],
        [("Authorization", "Bearer fake-credential-api")],
    ],
)
def test_guarded_route_rejects_duplicate_pin_and_mixed_authority(extra):
    client, services, audit = client_and_services()
    raw = request()
    response = client.post(SDK_ROUTE, content=raw, headers=sdk_headers(raw) + extra)
    assert response.status_code == 403
    assert services.storage.query() == []
    assert audit.records == []


def test_guarded_route_requires_profile_and_no_query_string():
    client, services, audit = client_and_services()
    raw = request()
    for route, headers in (
        (SDK_ROUTE, sdk_headers(raw)[:2]),
        (SDK_ROUTE + "?profile=" + PROFILE, sdk_headers(raw)),
    ):
        assert client.post(route, content=raw, headers=headers).status_code == 403
    assert services.storage.query() == []
    assert audit.records == []


def test_non_ascii_pin_is_denied_without_echo():
    client, services, audit = client_and_services()
    raw = request()
    headers = [(name.encode(), value.encode()) for name, value in sdk_headers(raw)[:2]]
    headers.append((RECEIVER_PROFILE_HEADER.encode(), b"\xff" * 64))
    response = client.post(SDK_ROUTE, content=raw, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == ""
    assert services.storage.query() == []
    assert audit.records == []


def test_profile_uses_installed_origin_not_host_or_origin_headers():
    client, _, _ = client_and_services()
    raw = request()
    response = client.post(
        SDK_ROUTE,
        content=raw,
        headers=sdk_headers(raw)
        + [
            ("Host", "substituted.invalid"),
            ("Origin", "https://substituted.invalid"),
        ],
    )
    assert response.status_code == 201


def test_legacy_route_still_accepts_original_projection_without_profile():
    client, _, _ = client_and_services()
    assert post(client, request()).status_code == 201


@pytest.mark.parametrize(
    "changed",
    [
        "issuer",
        "audience",
        "actor_id",
        "session_id",
        "correlation_id",
        "request",
        "idempotency_digest",
    ],
)
def test_final_verification_cannot_change_initial_identity_or_request(changed):
    raw = request()
    initial = SecSWorkVerifier(config()).verify(
        request_json=raw,
        projection_json=proof(raw),
        idempotency_key=KEY,
    )
    if changed == "request":
        final = replace(initial, request=WorkRequest.from_json(request(id="substituted")))
    elif changed == "idempotency_digest":
        final = replace(initial, idempotency_digest="0" * 64)
    else:
        final = replace(initial, principal=replace(initial.principal, **{changed: "changed"}))
    storage, audit = MemoryGraphStorage(), AuditLog()
    adapter = SecSWorkAdapter(
        storage=storage,
        audit_log=audit,
        verifier=Mock(verify=Mock(side_effect=[initial, final])),
    )
    with pytest.raises(SecSWorkDenied, match="named_work_admission_binding_changed"):
        adapter.execute(request_json=raw, projection_json=proof(raw), idempotency_key=KEY)
    assert storage.query() == []
    assert audit.records == []


def test_commit_and_audit_use_final_verified_objects(monkeypatch):
    raw = request()
    initial = SecSWorkVerifier(config()).verify(
        request_json=raw,
        projection_json=proof(raw),
        idempotency_key=KEY,
    )
    final = replace(
        initial,
        principal=replace(initial.principal),
        safe_summary={**initial.safe_summary, "admission_phase": "final"},
    )
    storage, audit = MemoryGraphStorage(), AuditLog()
    adapter = SecSWorkAdapter(
        storage=storage,
        audit_log=audit,
        verifier=Mock(verify=Mock(side_effect=[initial, final])),
    )
    capture = Mock(wraps=adapter.outbox.record_named_work_v1_with_receipt)
    monkeypatch.setattr(adapter.outbox, "record_named_work_v1_with_receipt", capture)
    adapter.execute(request_json=raw, projection_json=proof(raw), idempotency_key=KEY)
    assert capture.call_args.kwargs["principal"] is final.principal
    assert capture.call_args.kwargs["summary"] is final.safe_summary
    assert audit.records[0].safe_summary["admission_phase"] == "final"


def test_verification_captures_one_configuration_instance():
    replacement = replace(config(), stable_issuer="secs:replaced-during-verification")
    verifier = SecSWorkVerifier(config())

    def swap_config_during_clock_read():
        verifier.config = replacement
        return replacement.clock()

    verifier.config = replace(config(), clock=swap_config_during_clock_read)
    raw = request()
    verified = verifier.verify(
        request_json=raw, projection_json=proof(raw), idempotency_key=KEY, receiver_profile=PROFILE
    )
    assert verified.principal.issuer == "secs:test-work"
    with pytest.raises(SecSWorkDenied, match="sdk_receiver_profile_mismatch"):
        verifier.verify(
            request_json=raw,
            projection_json=proof(raw),
            idempotency_key=KEY,
            receiver_profile=PROFILE,
        )


def test_sdk_work_route_does_not_admit_arena_domain():
    from tests.api.test_arenas import arena_proof, create_request

    client, services, audit = client_and_services()
    raw = create_request()
    response = client.post(
        SDK_ROUTE,
        content=raw,
        headers={
            "X-Devgraph-Work-Authority": b64(arena_proof(raw)),
            "Idempotency-Key": KEY,
            RECEIVER_PROFILE_HEADER: PROFILE,
        },
    )
    assert response.status_code == 403
    assert services.storage.query() == []
    assert audit.records == []


def test_guarded_workflow_assign_review_transition_and_exact_retry():
    from devgraph.model.repository import WorkObjectRepository
    from devgraph.model.work import Task
    from devgraph.workflow_contract import decode_state

    client, services, _ = client_and_services()
    WorkObjectRepository(services.storage).create(
        Task(id="legacy-workflow", title="Legacy task", workflow_json=None)
    )
    operations = [
        ("workflow.assign", {"workflow_id": "execution.v1", "reason": "Explicit classification"}),
        (
            "workflow.review",
            {
                "record_id": "requirements-review",
                "phase": "requirements",
                "verdict": "approved",
                "summary": "Recorded agreed scope",
                "evidence": [
                    {
                        "kind": "ExternalLink",
                        "id": "scope-evidence",
                        "title": "Scope",
                        "url": "https://example.test/scope",
                    }
                ],
                "requirements": [
                    {
                        "id": "outcome",
                        "title": "Agreed outcome",
                        "outcome": "met",
                        "evidence_ids": ["scope-evidence"],
                    }
                ],
            },
        ),
        ("workflow.transition", {"stage": "intake"}),
    ]
    for version, (operation, payload) in enumerate(operations, 1):
        raw = request(operation, "Task", "legacy-workflow", version=version, payload=payload)
        key = KEY + f"-workflow-{version}"
        headers = {
            "X-Devgraph-Work-Authority": b64(proof(raw, key)),
            "Idempotency-Key": key,
            RECEIVER_PROFILE_HEADER: PROFILE,
        }
        first = client.post(SDK_ROUTE, content=raw, headers=headers)
        assert first.status_code == 200, first.text
        assert set(first.json()) == {"work", "receipt"}
        assert "workflow_json" not in first.json()["work"]
        assert first.json()["work"]["version"] == version + 1
        again = client.post(SDK_ROUTE, content=raw, headers=headers)
        assert again.status_code == 200 and again.json()["receipt"]["duplicate"]
        assert again.json()["receipt"]["receipt_id"] == first.json()["receipt"]["receipt_id"]
    state = services.storage.get_node("Task", "legacy-workflow").properties["workflow_json"]
    assert decode_state(state).stage == "intake"
    review = services.storage.get_node("ReviewPacket", "requirements-review")
    assert json.loads(review.properties["description"])["actor_id"] == "pubkey:sha256:" + "a" * 64


def test_progress_v2_guarded_receiver_retains_exact_profile_and_retry_binding():
    from tests.api.test_todo_progress_v2 import raw

    client, services, audit = client_and_services()
    request_bytes = raw()
    route = "/sdk/todo-operations/v2"
    assert (
        client.post(
            route, content=request_bytes, headers=sdk_headers(request_bytes, "0" * 64)
        ).status_code
        == 403
    )
    assert not services.storage.query("Todo")
    first = client.post(route, content=request_bytes, headers=sdk_headers(request_bytes))
    assert first.status_code == 201, first.text
    assert first.json()["work"]["progress"] == "not_started"
    retry = client.post(route, content=request_bytes, headers=sdk_headers(request_bytes))
    assert retry.status_code == 200 and retry.json()["receipt"]["duplicate"]
    assert (
        client.post(
            SDK_ROUTE, content=request_bytes, headers=sdk_headers(request_bytes)
        ).status_code
        == 403
    )
    assert len(services.storage.query("EventReceipt")) == 1
