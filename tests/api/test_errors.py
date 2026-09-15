"""Commit 7 (reordered before routes with recorded reason): safe errors.

Every failure path returns an RFC 7807-compatible ProblemDetail with no
credential, payload, or raw-key echo. All markers are synthetic.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.api.errors import register_error_handlers
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.events.outbox import IdempotencyScopeConflict
from devgraph.model.lifecycle import ProposalLifecycleError
from devgraph.model.repository import (
    InvalidStatusTransitionError,
    MissingWorkObjectError,
    UnknownWorkObjectKindError,
    WorkObjectAlreadyExistsError,
    WorkObjectVersionConflictError,
)

FAKE_MARKER = "FAKE-SECRET-api-err-9"


def _app_with_failing_route(exc: Exception) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


class TestProblemDetails:
    def test_unauthenticated_maps_to_401(self) -> None:
        client = _app_with_failing_route(UnauthenticatedError("credential required"))
        response = client.get("/boom")
        assert response.status_code == 401
        body = response.json()
        assert body["title"] == "Unauthenticated"
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_forbidden_maps_to_403_with_correlation_id(self) -> None:
        client = _app_with_failing_route(
            ForbiddenError("scope for category 'write' not granted", correlation_id="corr-9")
        )
        body = client.get("/boom").json()
        assert body["status"] == 403
        assert body["correlation_id"] == "corr-9"

    def test_lifecycle_conflict_maps_to_409(self) -> None:
        client = _app_with_failing_route(
            ProposalLifecycleError("Only accepted proposals can convert to Issue")
        )
        assert client.get("/boom").status_code == 409

    def test_lifecycle_missing_maps_to_404(self) -> None:
        client = _app_with_failing_route(ProposalLifecycleError("missing Proposal: p-9"))
        assert client.get("/boom").status_code == 404

    def test_idempotency_scope_conflict_maps_to_409(self) -> None:
        client = _app_with_failing_route(
            IdempotencyScopeConflict(
                "idempotency key digest already exists for a different operation or subject"
            )
        )
        assert client.get("/boom").status_code == 409


class TestNoEcho:
    def test_credential_shaped_detail_is_scrubbed(self) -> None:
        client = _app_with_failing_route(
            ValueError(f"rejected header Authorization: Bearer {FAKE_MARKER}")
        )
        response = client.get("/boom")
        assert response.status_code == 400
        assert FAKE_MARKER not in json.dumps(response.json())

    def test_validation_errors_never_echo_input_values(self) -> None:
        app = FastAPI()
        register_error_handlers(app)

        from devgraph.api.schemas import AcceptProposalRequest

        @app.post("/strict")
        def strict(request: AcceptProposalRequest):  # pragma: no cover - never reached
            return {}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/strict",
            json={
                "decision_id": "d-1",
                "decision_title": "ok",
                "credential": FAKE_MARKER,
            },
        )
        assert response.status_code == 422
        assert FAKE_MARKER not in json.dumps(response.json())


def test_repository_errors_have_dedicated_safe_mappings() -> None:
    cases = [
        (MissingWorkObjectError("missing Issue: i-1"), 404, "Work object not found"),
        (WorkObjectAlreadyExistsError("existing Issue: i-1"), 409, "Work object already exists"),
        (
            InvalidStatusTransitionError("invalid transition draft -> archived for Issue: i-1"),
            409,
            "Invalid status transition",
        ),
        (
            UnknownWorkObjectKindError("unknown work-object kind: Nope"),
            400,
            "Unknown work object kind",
        ),
        (
            WorkObjectVersionConflictError("Issue", "i-1", expected_version=1, actual_version=2),
            412,
            "Version precondition failed",
        ),
    ]
    for error, status, title in cases:
        response = _app_with_failing_route(error).get("/boom")
        assert (response.status_code, response.json()["title"]) == (status, title)
