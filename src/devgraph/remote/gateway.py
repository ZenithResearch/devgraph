"""Bounded transport-agnostic remote dispatch gateway."""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from devgraph.client import (
    DevgraphClientError,
    DevgraphInvalidSuccessEnvelope,
    DevgraphMalformedProblem,
    DevgraphMalformedSuccess,
    DevgraphProblem,
    DevgraphRequestContext,
    DevgraphTimeout,
    DevgraphTransportError,
    DevgraphUnexpectedContentType,
)
from devgraph.remote.contracts import (
    REMOTE_ENVELOPE_ADAPTER,
    RemoteCredentialValidationError,
    RemoteIngressCredential,
)

_REQUEST_ID = re.compile(r"^rmt_[a-z2-7]{26}$")


class ResponseProjector(Protocol):
    def success(self, *, request_id: str, operation: str, value: object) -> object: ...

    def failure(
        self,
        *,
        request_id: str,
        operation: str,
        reason_code: str,
        correlation_id: str | None = None,
    ) -> object: ...


def generate_request_id() -> str:
    encoded = base64.b32encode(secrets.token_bytes(16)).decode("ascii")
    return f"rmt_{encoded.rstrip('=').lower()}"


class RemoteGateway:
    """Validate a generic command and synchronously reuse the bounded client."""

    def __init__(
        self,
        *,
        client: Any,
        projector: ResponseProjector,
        request_id_factory: Callable[[], str] = generate_request_id,
    ) -> None:
        self._client = client
        self._projector = projector
        self._request_id_factory = request_id_factory

    def dispatch(self, payload: object, *, credential: object) -> object:
        request_id = self._request_id_factory()
        if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
            return self._projector.failure(
                request_id="rmt_aaaaaaaaaaaaaaaaaaaaaaaaaa",
                operation="unrecognized",
                reason_code="invalid_remote_request",
            )
        try:
            holder = RemoteIngressCredential.model_validate({"credential": credential})
        except RemoteCredentialValidationError:
            return self._projector.failure(
                request_id=request_id,
                operation="unrecognized",
                reason_code="credential_required" if credential == "" else "invalid_remote_request",
            )

        try:
            envelope = REMOTE_ENVELOPE_ADAPTER.validate_python(payload)
        except (ValidationError, TypeError, ValueError):
            return self._projector.failure(
                request_id=request_id,
                operation="unrecognized",
                reason_code="invalid_remote_request",
            )

        context = DevgraphRequestContext(credential=holder.credential)
        operation = envelope.operation
        arguments = envelope.arguments.model_dump()
        method = getattr(self._client, operation)
        try:
            value = method(context, **arguments)
        except DevgraphClientError as exc:
            return self._projector.failure(
                request_id=request_id,
                operation=operation,
                reason_code=_classify_client_error(exc),
                correlation_id=exc.correlation_id if isinstance(exc, DevgraphProblem) else None,
            )
        return self._projector.success(
            request_id=request_id,
            operation=operation,
            value=value,
        )


def _classify_client_error(error: DevgraphClientError) -> str:
    if isinstance(error, DevgraphProblem):
        if error.status == 401:
            return "unauthenticated"
        if error.status == 403:
            return "forbidden"
        if error.status == 404:
            return "not_found"
        if error.status == 409:
            if error.title == "Idempotency scope conflict":
                return "idempotency_scope_conflict"
            return "conflict"
        if error.status == 412:
            return "precondition_failed"
        if error.status in {400, 422}:
            return "invalid_remote_request"
        return "upstream_error"
    if isinstance(error, DevgraphTimeout):
        return "upstream_timeout"
    if isinstance(error, DevgraphTransportError):
        return "upstream_unavailable"
    if isinstance(
        error,
        (
            DevgraphInvalidSuccessEnvelope,
            DevgraphMalformedProblem,
            DevgraphMalformedSuccess,
            DevgraphUnexpectedContentType,
        ),
    ):
        return "malformed_upstream"
    return "upstream_error"
