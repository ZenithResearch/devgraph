from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from remote_authority_fixtures import build_remote_authority_fixture

from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL
from devgraph.model.work import Issue

OPERATIONS = (
    "create_issue",
    "get_issue",
    "list_issues",
    "transition_issue_to_review",
)
DENIALS = (
    ("unknown", False, "unauthenticated"),
    ("expired", False, "unauthenticated"),
    ("wrong_audience", False, "unauthenticated"),
    ("wrong_scope", False, "forbidden"),
    ("full", True, "unauthenticated"),
)


def payload(operation: str) -> dict[str, Any]:
    if operation == "create_issue":
        return {
            "operation": operation,
            "arguments": {
                "work_id": "remote-denied-issue",
                "title": "remote-private-title",
                "idempotency_key": "remote-private-key",
            },
        }
    if operation == "get_issue":
        return {"operation": operation, "arguments": {"work_id": "remote-denied-issue"}}
    if operation == "list_issues":
        return {"operation": operation, "arguments": {}}
    return {
        "operation": operation,
        "arguments": {
            "work_id": "remote-denied-issue",
            "idempotency_key": "remote-review-key",
        },
    }


def counts(fixture) -> tuple[int, int, int, int]:
    storage = fixture.observation.storage
    return (
        len(storage.query("Issue")),
        len(fixture.observation.audit_log.records),
        len(storage.query(EVENT_RECEIPT_LABEL)),
        len(storage.list_edges(EMITTED_EVENT)),
    )


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(("credential_name", "unavailable", "reason_code"), DENIALS)
def test_remote_denied_requests_never_reach_handler_or_change_state(
    operation: str,
    credential_name: str,
    unavailable: bool,
    reason_code: str,
) -> None:
    fixture = build_remote_authority_fixture(verifier_available=not unavailable)
    if operation != "create_issue":
        fixture.observation.repository.create(Issue(id="remote-denied-issue", title="seed"))
    fixture.observation.repository.reset_calls()
    before = counts(fixture)
    credential = getattr(fixture.credentials, credential_name)

    response = fixture.gateway.dispatch(payload(operation), credential=credential)

    assert response.reason_code == reason_code
    assert response.operation == operation
    assert counts(fixture) == before
    assert fixture.observation.repository.calls == {
        "get_by_id": 0,
        "query": 0,
        "create": 0,
        "transition_status": 0,
    }
    rendered = response.model_dump_json() + str(response) + repr(response)
    for private in (
        credential,
        "remote-private-title",
        "remote-private-key",
        "remote-review-key",
    ):
        assert private not in rendered


def test_missing_credential_denied_before_client_and_state_observation() -> None:
    fixture = build_remote_authority_fixture()
    before = counts(fixture)
    fixture.observation.repository.reset_calls()

    response = fixture.gateway.dispatch(payload("create_issue"), credential="")

    assert response.reason_code == "credential_required"
    assert response.operation == "unrecognized"
    assert counts(fixture) == before
    assert all(value == 0 for value in fixture.observation.repository.calls.values())


def test_denied_matrix_covers_every_operation_and_required_denial() -> None:
    assert set(OPERATIONS) == {
        "create_issue",
        "get_issue",
        "list_issues",
        "transition_issue_to_review",
    }
    assert {name for name, _, _ in DENIALS} == {
        "unknown",
        "expired",
        "wrong_audience",
        "wrong_scope",
        "full",
    }


def test_authorized_remote_sequence_reuses_real_client_api_and_outbox() -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full

    created = fixture.gateway.dispatch(payload("create_issue"), credential=credential)
    fetched = fixture.gateway.dispatch(payload("get_issue"), credential=credential)
    listed = fixture.gateway.dispatch(payload("list_issues"), credential=credential)
    transitioned = fixture.gateway.dispatch(
        payload("transition_issue_to_review"), credential=credential
    )

    assert created.outcome == "accepted" and created.duplicate is False
    assert fetched.model_dump() == {
        "request_id": "rmt_abcdefghijklmnopqrstuvwxyz",
        "operation": "get_issue",
        "outcome": "found",
    }
    assert listed.item_count == 1
    assert transitioned.outcome == "accepted" and transitioned.duplicate is False
    assert fixture.observation.repository.calls == {
        "get_by_id": 1,
        "query": 1,
        "create": 1,
        "transition_status": 1,
    }
    assert len(fixture.observation.storage.query(EVENT_RECEIPT_LABEL)) == 2
    assert len(fixture.observation.storage.list_edges(EMITTED_EVENT)) == 2


def test_authorized_same_key_same_scope_returns_prior_receipt_without_mutation() -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full
    first_payload = payload("create_issue")
    second_payload = payload("create_issue")
    second_payload["arguments"]["title"] = "different-private-title"

    first = fixture.gateway.dispatch(first_payload, credential=credential)
    before = counts(fixture)
    duplicate = fixture.gateway.dispatch(second_payload, credential=credential)
    stored = fixture.observation.storage.query("Issue")

    assert duplicate.duplicate is True
    assert duplicate.receipt_id == first.receipt_id
    assert counts(fixture) == before
    assert len(stored) == 1
    assert stored[0].properties["title"] == "remote-private-title"


@pytest.mark.parametrize(
    "second_payload",
    [
        {
            "operation": "create_issue",
            "arguments": {
                "work_id": "remote-other-issue",
                "title": "other",
                "idempotency_key": "remote-private-key",
            },
        },
        {
            "operation": "transition_issue_to_review",
            "arguments": {
                "work_id": "remote-denied-issue",
                "idempotency_key": "remote-private-key",
            },
        },
    ],
)
def test_authorized_same_key_different_scope_is_safe_conflict(
    second_payload: dict[str, Any],
) -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full
    fixture.gateway.dispatch(payload("create_issue"), credential=credential)
    before = counts(fixture)

    response = fixture.gateway.dispatch(second_payload, credential=credential)

    assert response.reason_code == "idempotency_scope_conflict"
    assert counts(fixture) == before


def test_authorized_request_id_never_controls_idempotency() -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full
    first = payload("create_issue")
    second = payload("create_issue")
    second["arguments"].update(
        work_id="remote-second-issue",
        idempotency_key="remote-second-key",
    )

    first_result = fixture.gateway.dispatch(first, credential=credential)
    second_result = fixture.gateway.dispatch(second, credential=credential)

    assert first_result.receipt_id != second_result.receipt_id
    assert len(fixture.observation.storage.query(EVENT_RECEIPT_LABEL)) == 2


def test_audit_and_receipt_correlation_come_from_verified_authority() -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full

    created = fixture.gateway.dispatch(payload("create_issue"), credential=credential)
    fixture.gateway.dispatch(payload("get_issue"), credential=credential)
    fixture.gateway.dispatch(payload("list_issues"), credential=credential)
    transitioned = fixture.gateway.dispatch(
        payload("transition_issue_to_review"), credential=credential
    )

    records = fixture.observation.audit_log.records
    assert [record.operation for record in records] == [
        "create_work_object",
        "get_work_object",
        "query_work_objects",
        "transition_work_object_status",
    ]
    assert {record.actor_id for record in records} == {"synthetic-actor-full"}
    assert {record.session_id for record in records} == {"synthetic-session-full"}
    assert {record.correlation_id for record in records} == {"synthetic-correlation-full"}
    assert created.correlation_id == "synthetic-correlation-full"
    assert transitioned.correlation_id == "synthetic-correlation-full"


def test_get_and_list_carry_no_authority_identifiers_or_private_context() -> None:
    fixture = build_remote_authority_fixture()
    credential = fixture.credentials.full
    fixture.gateway.dispatch(payload("create_issue"), credential=credential)

    fetched = fixture.gateway.dispatch(payload("get_issue"), credential=credential)
    listed = fixture.gateway.dispatch(payload("list_issues"), credential=credential)
    rendered = fetched.model_dump_json() + listed.model_dump_json()

    for forbidden in (
        "synthetic-actor-full",
        "synthetic-session-full",
        "synthetic-correlation-full",
        credential,
    ):
        assert forbidden not in rendered


def test_caller_authority_correlation_override_is_denied_before_audit() -> None:
    fixture = build_remote_authority_fixture()
    malicious = payload("get_issue")
    malicious["actor_id"] = "caller-actor"
    malicious["correlation_id"] = "caller-correlation"

    response = fixture.gateway.dispatch(malicious, credential=fixture.credentials.full)

    assert response.reason_code == "invalid_remote_request"
    assert response.operation == "unrecognized"
    assert fixture.observation.audit_log.records == []


def test_gateway_never_reads_verifier_or_audit_internals_for_correlation() -> None:
    source = (Path(__file__).resolve().parents[2] / "src/devgraph/remote/gateway.py").read_text()

    for forbidden in (
        "._verifier",
        "._audit_log",
        ".actor_id",
        ".session_id",
    ):
        assert forbidden not in source
