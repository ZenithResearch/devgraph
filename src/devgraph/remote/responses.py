"""Fail-closed transport-safe response projection."""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from devgraph.client import MutationResult, WorkObject, WorkObjectList
from devgraph.model.validation import validate_work_object_id

_REQUEST_ID = re.compile(r"^rmt_[a-z2-7]{26}$")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SafeOperation: TypeAlias = Literal[
    "create_issue",
    "get_issue",
    "list_issues",
    "transition_issue_to_review",
    "unrecognized",
]
ReasonCode: TypeAlias = Literal[
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
]

PROBLEM_TABLE: dict[str, tuple[int, str, str]] = {
    "invalid_remote_request": (
        400,
        "Invalid remote request",
        "urn:devgraph:remote:invalid-request",
    ),
    "credential_required": (
        401,
        "Credential required",
        "urn:devgraph:remote:credential-required",
    ),
    "unauthenticated": (401, "Unauthenticated", "urn:devgraph:remote:unauthenticated"),
    "forbidden": (403, "Forbidden", "urn:devgraph:remote:forbidden"),
    "not_found": (404, "Not found", "urn:devgraph:remote:not-found"),
    "conflict": (409, "Conflict", "urn:devgraph:remote:conflict"),
    "idempotency_scope_conflict": (
        409,
        "Idempotency scope conflict",
        "urn:devgraph:remote:idempotency-scope-conflict",
    ),
    "precondition_failed": (
        412,
        "Precondition failed",
        "urn:devgraph:remote:precondition-failed",
    ),
    "upstream_timeout": (504, "Upstream timeout", "urn:devgraph:remote:upstream-timeout"),
    "upstream_unavailable": (
        503,
        "Upstream unavailable",
        "urn:devgraph:remote:upstream-unavailable",
    ),
    "malformed_upstream": (
        502,
        "Malformed upstream response",
        "urn:devgraph:remote:malformed-upstream",
    ),
    "upstream_error": (502, "Upstream error", "urn:devgraph:remote:upstream-error"),
}
_VALID_OPERATIONS = {
    "create_issue",
    "get_issue",
    "list_issues",
    "transition_issue_to_review",
}


class _StrictFrozenResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )

    @field_validator("request_id", check_fields=False)
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if _REQUEST_ID.fullmatch(value) is None:
            raise ValueError("invalid request id")
        return value


class RemoteMutationAccepted(_StrictFrozenResponse):
    request_id: str
    operation: Literal["create_issue", "transition_issue_to_review"]
    outcome: Literal["accepted"]
    receipt_id: str
    receipt_status: Literal["pending", "dispatched_dry_run", "retry_scheduled", "failed"]
    duplicate: bool
    correlation_id: str

    @field_validator("receipt_id")
    @classmethod
    def validate_receipt_id(cls, value: str) -> str:
        return validate_work_object_id(value)

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, value: str) -> str:
        if _CORRELATION_ID.fullmatch(value) is None:
            raise ValueError("invalid correlation id")
        return value


class RemoteFound(_StrictFrozenResponse):
    request_id: str
    operation: Literal["get_issue"]
    outcome: Literal["found"]


class RemoteListed(_StrictFrozenResponse):
    request_id: str
    operation: Literal["list_issues"]
    outcome: Literal["listed"]
    item_count: int = Field(ge=0, le=50)


class RemoteProblemResponse(_StrictFrozenResponse):
    request_id: str
    operation: SafeOperation
    outcome: Literal["denied", "error"]
    reason_code: ReasonCode
    status: int
    title: str
    type: str
    correlation_id: str | None = None

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, value: str | None) -> str | None:
        if value is not None and _CORRELATION_ID.fullmatch(value) is None:
            raise ValueError("invalid correlation id")
        return value


class TransportSafeProjector:
    """Project strict client models into minimal transport-safe responses."""

    def success(self, *, request_id: str, operation: str, value: object) -> _StrictFrozenResponse:
        safe_operation = _safe_operation(operation)
        try:
            if safe_operation in {"create_issue", "transition_issue_to_review"}:
                if not isinstance(value, MutationResult):
                    return self._malformed(request_id=request_id, operation=safe_operation)
                receipt = value.receipt
                return RemoteMutationAccepted(
                    request_id=request_id,
                    operation=safe_operation,
                    outcome="accepted",
                    receipt_id=receipt.receipt_id,
                    receipt_status=receipt.receipt_status,
                    duplicate=receipt.duplicate,
                    correlation_id=receipt.correlation_id,
                )
            if safe_operation == "get_issue":
                if not isinstance(value, WorkObject):
                    return self._malformed(request_id=request_id, operation=safe_operation)
                return RemoteFound(
                    request_id=request_id,
                    operation="get_issue",
                    outcome="found",
                )
            if safe_operation == "list_issues":
                if not isinstance(value, WorkObjectList):
                    return self._malformed(request_id=request_id, operation=safe_operation)
                return RemoteListed(
                    request_id=request_id,
                    operation="list_issues",
                    outcome="listed",
                    item_count=len(value.items),
                )
        except (ValidationError, TypeError, ValueError):
            return self._malformed(request_id=request_id, operation=safe_operation)
        return self._malformed(request_id=request_id, operation=safe_operation)

    def failure(
        self,
        *,
        request_id: str,
        operation: str,
        reason_code: str,
        correlation_id: str | None = None,
    ) -> RemoteProblemResponse:
        safe_operation = _safe_operation(operation)
        if reason_code not in PROBLEM_TABLE:
            reason_code = "upstream_error"
        if correlation_id is not None and _CORRELATION_ID.fullmatch(correlation_id) is None:
            reason_code = "malformed_upstream"
            correlation_id = None
        status, title, type_value = PROBLEM_TABLE[reason_code]
        outcome = (
            "error"
            if reason_code.startswith("upstream_") or reason_code == "malformed_upstream"
            else "denied"
        )
        return RemoteProblemResponse.model_validate(
            {
                "request_id": request_id,
                "operation": safe_operation,
                "outcome": outcome,
                "reason_code": reason_code,
                "status": status,
                "title": title,
                "type": type_value,
                "correlation_id": correlation_id,
            }
        )

    def _malformed(self, *, request_id: str, operation: str) -> RemoteProblemResponse:
        return self.failure(
            request_id=request_id,
            operation=operation,
            reason_code="malformed_upstream",
        )


def _safe_operation(value: str) -> SafeOperation:
    if value in _VALID_OPERATIONS:
        return value  # type: ignore[return-value]
    return "unrecognized"
