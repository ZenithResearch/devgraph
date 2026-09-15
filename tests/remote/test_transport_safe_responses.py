from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from devgraph.client import (
    DevgraphHttpClient,
    MutationReceipt,
    MutationResult,
    WorkObject,
    WorkObjectList,
)
from devgraph.remote import RemoteGateway
from devgraph.remote.responses import (
    PROBLEM_TABLE,
    RemoteFound,
    RemoteListed,
    RemoteMutationAccepted,
    RemoteProblemResponse,
    TransportSafeProjector,
)

REQUEST_ID = "rmt_abcdefghijklmnopqrstuvwxyz"


def work(index: int = 1) -> WorkObject:
    return WorkObject(
        id=f"private-issue-{index}",
        kind="Issue",
        title=f"private-title-{index}",
        description="private-description",
        status="draft",
        version=1,
        priority=0,
        artifact_ids=("private-artifact",),
        external_link_ids=("private-link",),
    )


def receipt(**overrides: object) -> MutationReceipt:
    values: dict[str, object] = {
        "receipt_id": "event-receipt-safe-one",
        "operation": "create_work_object",
        "subject_label": "Issue",
        "subject_id": "private-issue-1",
        "receipt_status": "pending",
        "duplicate": False,
        "correlation_id": "safe-correlation-1",
    }
    values.update(overrides)
    return MutationReceipt.model_validate(values)


def test_mutation_success_projects_only_closed_receipt_metadata() -> None:
    projector = TransportSafeProjector()
    raw = MutationResult(work=work(), receipt=receipt())

    response = projector.success(
        request_id=REQUEST_ID,
        operation="create_issue",
        value=raw,
    )

    assert isinstance(response, RemoteMutationAccepted)
    assert response.model_dump() == {
        "request_id": REQUEST_ID,
        "operation": "create_issue",
        "outcome": "accepted",
        "receipt_id": "event-receipt-safe-one",
        "receipt_status": "pending",
        "duplicate": False,
        "correlation_id": "safe-correlation-1",
    }
    rendered = response.model_dump_json() + repr(response) + str(response)
    for private in (
        "private-issue",
        "private-title",
        "private-description",
        "private-artifact",
        "private-link",
        "create_work_object",
    ):
        assert private not in rendered


def test_get_and_list_project_only_presence_and_bounded_count() -> None:
    projector = TransportSafeProjector()

    found = projector.success(request_id=REQUEST_ID, operation="get_issue", value=work())
    listed = projector.success(
        request_id=REQUEST_ID,
        operation="list_issues",
        value=WorkObjectList(items=(work(1), work(2))),
    )

    assert isinstance(found, RemoteFound)
    assert found.model_dump() == {
        "request_id": REQUEST_ID,
        "operation": "get_issue",
        "outcome": "found",
    }
    assert isinstance(listed, RemoteListed)
    assert listed.model_dump() == {
        "request_id": REQUEST_ID,
        "operation": "list_issues",
        "outcome": "listed",
        "item_count": 2,
    }


def test_problem_table_is_closed_and_emitted_exactly() -> None:
    projector = TransportSafeProjector()

    assert set(PROBLEM_TABLE) == {
        "invalid_remote_request",
        "credential_required",
        "unauthenticated",
        "forbidden",
        "not_found",
        "conflict",
        "idempotency_scope_conflict",
        "precondition_failed",
        "upstream_timeout",
        "upstream_unavailable",
        "malformed_upstream",
        "upstream_error",
    }
    for reason_code, (status, title, type_value) in PROBLEM_TABLE.items():
        response = projector.failure(
            request_id=REQUEST_ID,
            operation="get_issue",
            reason_code=reason_code,
        )
        assert response.model_dump() == {
            "request_id": REQUEST_ID,
            "operation": "get_issue",
            "outcome": "error"
            if reason_code.startswith("upstream_") or reason_code == "malformed_upstream"
            else "denied",
            "reason_code": reason_code,
            "status": status,
            "title": title,
            "type": type_value,
            "correlation_id": None,
        }


def test_unknown_operation_is_fixed_unrecognized_and_never_echoed() -> None:
    response = TransportSafeProjector().failure(
        request_id=REQUEST_ID,
        operation="attacker-operation\nsecret",
        reason_code="invalid_remote_request",
    )

    assert response.operation == "unrecognized"
    assert "attacker" not in response.model_dump_json()


@pytest.mark.parametrize(
    "raw",
    [
        {"unknown": {"nested": "private"}},
        MutationResult(work=work(), receipt=receipt(receipt_id="UPPER")),
        MutationResult(work=work(), receipt=receipt(correlation_id="bad\ncorrelation")),
        WorkObjectList(items=tuple(work(index) for index in range(51))),
    ],
)
def test_invalid_upstream_success_scalar_fails_closed_without_echo(raw: object) -> None:
    response = TransportSafeProjector().success(
        request_id=REQUEST_ID,
        operation="create_issue" if not isinstance(raw, WorkObjectList) else "list_issues",
        value=raw,
    )

    assert isinstance(response, RemoteProblemResponse)
    assert response.reason_code == "malformed_upstream"
    rendered = response.model_dump_json() + repr(response)
    for private in ("private", "UPPER", "bad", "create_work_object"):
        assert private not in rendered


@pytest.mark.parametrize("field", ["receipt_status", "operation", "subject_label"])
def test_invalid_wire_receipt_is_rejected_by_current_client_and_projected_safely(
    field: str,
) -> None:
    raw = MutationResult(work=work(), receipt=receipt()).model_dump(mode="json")
    raw["receipt"][field] = "private-invalid-wire-value"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/work/Issue"
        return httpx.Response(201, json=raw)

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        gateway = RemoteGateway(
            client=DevgraphHttpClient(
                transport=transport, base_url="http://in-process", timeout=1.0
            ),
            projector=TransportSafeProjector(),
            request_id_factory=lambda: REQUEST_ID,
        )
        response = gateway.dispatch(
            {
                "operation": "create_issue",
                "arguments": {
                    "work_id": "private-issue-1",
                    "title": "private-title-1",
                    "idempotency_key": "synthetic-wire-check",
                },
            },
            credential="synthetic-wire-credential",
        )

    assert isinstance(response, RemoteProblemResponse)
    assert response.reason_code == "malformed_upstream"
    assert response.status == 502
    assert response.correlation_id is None
    rendered = response.model_dump_json() + repr(response) + str(response)
    for value in ("private-", "synthetic-wire-credential", "create_work_object"):
        assert value not in rendered


def test_invalid_optional_problem_correlation_is_malformed_upstream() -> None:
    response = TransportSafeProjector().failure(
        request_id=REQUEST_ID,
        operation="get_issue",
        reason_code="forbidden",
        correlation_id="private\ncorrelation",
    )

    assert response.reason_code == "malformed_upstream"
    assert response.correlation_id is None


def test_response_models_are_strict_frozen_and_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RemoteFound.model_validate(
            {
                "request_id": REQUEST_ID,
                "operation": "get_issue",
                "outcome": "found",
                "private": "leak",
            }
        )

    response = RemoteFound(
        request_id=REQUEST_ID,
        operation="get_issue",
        outcome="found",
    )
    with pytest.raises(ValidationError):
        response.request_id = "changed"


def test_safe_output_never_contains_raw_problem_or_exception_material() -> None:
    response = TransportSafeProjector().failure(
        request_id=REQUEST_ID,
        operation="get_issue",
        reason_code="upstream_error",
        correlation_id="safe-correlation-1",
    )
    rendered = json.dumps(response.model_dump()) + repr(response) + str(response)

    assert "safe-correlation-1" in rendered
    assert "detail" not in rendered
    assert "traceback" not in rendered.lower()
