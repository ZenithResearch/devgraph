"""RFC 7807-compatible safe error envelopes for the API adapter.

Every handler builds a ProblemDetail whose strings pass through the
Issue 8 redaction seam — defense in depth on top of AuthError's own
construction-time scrubbing. No handler echoes credentials, request
payloads, envelope claims, or raw idempotency keys; AuthError's safe
correlation id is the only identifying value carried.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from devgraph.api.schemas import ProblemDetail
from devgraph.auth.errors import AuthError, ForbiddenError, UnauthenticatedError
from devgraph.events.outbox import IdempotencyScopeConflict
from devgraph.model.initiative_observations import (
    InitiativeObservationAlreadyExistsError,
    InvalidInitiativeObservationError,
    MissingInitiativeObservationError,
)
from devgraph.model.lifecycle import ProposalLifecycleError
from devgraph.model.repository import (
    InvalidStatusTransitionError,
    MissingWorkObjectError,
    UnknownWorkObjectKindError,
    WorkObjectAlreadyExistsError,
    WorkObjectVersionConflictError,
)
from devgraph.policy.redaction import redact_text
from devgraph.supporting_material import SupportingMaterialCursorChanged

_PROBLEM_CONTENT_TYPE = "application/problem+json"


class PreconditionRequiredError(ValueError):
    pass


class InvalidVersionPreconditionError(ValueError):
    pass


def _problem_response(problem: ProblemDetail) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(),
        media_type=_PROBLEM_CONTENT_TYPE,
    )


def register_error_handlers(app: FastAPI) -> None:
    from devgraph.arenas import ArenaConflict
    from devgraph.auth.secs_work import SecSWorkDenied
    from devgraph.named_work import WorkRelationshipConflict

    for exception, title, status in (
        (SecSWorkDenied, "Named Work authority denied", 403),
        (WorkRelationshipConflict, "Work relationship conflict", 409),
        (ArenaConflict, "Arena membership or archival conflict", 409),
    ):
        app.add_exception_handler(
            exception,
            lambda request, exc, title=title, status=status: _problem_response(
                ProblemDetail(title=title, status=status, detail="")
            ),
        )

    @app.exception_handler(PreconditionRequiredError)
    def precondition_required(request: Request, exc: PreconditionRequiredError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(title="Precondition required", status=428, detail="")
        )

    @app.exception_handler(InvalidVersionPreconditionError)
    def invalid_precondition(
        request: Request, exc: InvalidVersionPreconditionError
    ) -> JSONResponse:
        return _problem_response(
            ProblemDetail(title="Invalid version precondition", status=400, detail="")
        )

    def repository_problem(title: str, status: int, exc: Exception) -> JSONResponse:
        return _problem_response(
            ProblemDetail(title=title, status=status, detail=redact_text(str(exc)))
        )

    for exception, title, status in (
        (MissingWorkObjectError, "Work object not found", 404),
        (WorkObjectAlreadyExistsError, "Work object already exists", 409),
        (InvalidStatusTransitionError, "Invalid status transition", 409),
        (UnknownWorkObjectKindError, "Unknown work object kind", 400),
        (WorkObjectVersionConflictError, "Version precondition failed", 412),
        (MissingInitiativeObservationError, "Initiative observation not found", 404),
        (
            InitiativeObservationAlreadyExistsError,
            "Initiative observation already exists",
            409,
        ),
        (InvalidInitiativeObservationError, "Invalid initiative observation", 400),
    ):
        app.add_exception_handler(
            exception,
            lambda request, exc, title=title, status=status: repository_problem(title, status, exc),
        )

    @app.exception_handler(UnauthenticatedError)
    def unauthenticated(request: Request, exc: UnauthenticatedError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Unauthenticated",
                status=401,
                detail=redact_text(str(exc)),
                correlation_id=exc.correlation_id,
            )
        )

    @app.exception_handler(ForbiddenError)
    def forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Forbidden",
                status=403,
                detail=redact_text(str(exc)),
                correlation_id=exc.correlation_id,
            )
        )

    @app.exception_handler(AuthError)
    def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Authorization error",
                status=403,
                detail=redact_text(str(exc)),
                correlation_id=exc.correlation_id,
            )
        )

    @app.exception_handler(ProposalLifecycleError)
    def lifecycle_conflict(request: Request, exc: ProposalLifecycleError) -> JSONResponse:
        message = redact_text(str(exc))
        status = 404 if message.startswith("missing ") else 409
        return _problem_response(
            ProblemDetail(
                title="Lifecycle conflict" if status == 409 else "Not found",
                status=status,
                detail=message,
            )
        )

    @app.exception_handler(IdempotencyScopeConflict)
    def idempotency_conflict(request: Request, exc: IdempotencyScopeConflict) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Idempotency scope conflict",
                status=409,
                detail=redact_text(str(exc)),
            )
        )

    @app.exception_handler(KeyError)
    def missing(request: Request, exc: KeyError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Not found",
                status=404,
                detail=redact_text(str(exc.args[0]) if exc.args else ""),
            )
        )

    @app.exception_handler(SupportingMaterialCursorChanged)
    def supporting_changed(request: Request, exc: SupportingMaterialCursorChanged) -> JSONResponse:
        return _problem_response(
            ProblemDetail(title="Supporting material changed", status=409, detail=str(exc))
        )

    @app.exception_handler(ValueError)
    def invalid(request: Request, exc: ValueError) -> JSONResponse:
        return _problem_response(
            ProblemDetail(
                title="Invalid request",
                status=400,
                detail=redact_text(str(exc)),
            )
        )

    @app.exception_handler(RequestValidationError)
    def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo rejected input values — only field locations and kinds.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ()))}: "
            f"{error.get('type', 'invalid')}"
            for error in exc.errors()
        )
        return _problem_response(
            ProblemDetail(
                title="Validation failed",
                status=422,
                detail=redact_text(details),
            )
        )
