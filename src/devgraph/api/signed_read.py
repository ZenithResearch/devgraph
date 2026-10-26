"""Explicit, read-only v2 composition. No legacy bearer or write fallback."""

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from devgraph.api.native_read import _body
from devgraph.api.routes import _envelope
from devgraph.api.schemas import WorkKind, WorkObjectListEnvelope
from devgraph.auth.delegated_read import DelegatedReadService
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.auth.native_read import RESOURCE
from devgraph.auth.signed_read import SignedReadEnrollment
from devgraph.storage.base import GraphStorage


class ListParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: WorkKind
    limit: int = Field(default=20, ge=1, le=100)
    after_id: str | None = Field(default=None, max_length=256)
    include_archived: bool = False
    descending: bool = False


class GetParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: WorkKind
    work_id: str = Field(min_length=1, max_length=256)


def create_signed_read_app(
    *,
    storage: GraphStorage,
    enrollment: SignedReadEnrollment,
    audit_log: AuditLog,
    lifespan=None,
    web_wallet_url=None,
) -> FastAPI:
    app = FastAPI(
        title="Devgraph holder-bound read access",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse({"error": "access_denied"}, status_code=401)

    @app.exception_handler(ForbiddenError)
    async def forbidden(_request, _error):
        return JSONResponse({"error": "read_not_permitted"}, status_code=403)

    @app.exception_handler(KeyError)
    async def missing(_request, _error):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.middleware("http")
    async def private(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/devgraph/native-read/v2/challenges")
    async def challenge(request: Request):
        body = await _body(request, {"subject_public_key", "installation_id"})
        return await run_in_threadpool(
            enrollment.challenge, body["subject_public_key"], body["installation_id"]
        )

    @app.post("/devgraph/native-read/v2/enrollments")
    async def enroll(request: Request):
        body = await _body(request, {"challenge", "proof", "signature"})
        return await run_in_threadpool(enrollment.enroll, **body)

    if getattr(enrollment, "holder_grants", None) is not None:

        @app.post("/devgraph/native-read/v2/grants")
        async def holder_grant(request: Request):
            body = await _body(request, {"challenge", "proof", "signature"})
            return await run_in_threadpool(enrollment.holder_grants.issue, **body)

    def read(body):
        call = body["call"]
        if (
            not isinstance(call, dict)
            or set(call) != {"operation", "resource", "parameters"}
            or call["resource"] != RESOURCE
        ):
            raise UnauthenticatedError("unsupported_read")
        try:
            if call["operation"] == "devgraph.work.list":
                parameters = ListParameters.model_validate(call["parameters"])
            elif call["operation"] == "devgraph.work.get":
                parameters = GetParameters.model_validate(call["parameters"])
            else:
                raise UnauthenticatedError("unsupported_read")
        except ValidationError:
            raise UnauthenticatedError("invalid_request") from None
        context = enrollment.verify_request(**body)

        class VerifiedRequest:
            def verify(self, credential, *, audience):
                if credential is not None or audience != "devgraph":
                    raise UnauthenticatedError("invalid_request")
                return context

        service = DelegatedReadService(
            storage=storage, verifier=VerifiedRequest(), audience="devgraph", audit_log=audit_log
        )
        if isinstance(parameters, ListParameters):
            return WorkObjectListEnvelope(
                items=[
                    _envelope(item) for item in service.list_work(None, **parameters.model_dump())
                ]
            )
        return _envelope(service.get_work(None, **parameters.model_dump()))

    @app.post("/devgraph/native-read/v2/requests")
    async def request_read(request: Request):
        body = await _body(
            request,
            {"credential", "call", "proof"},
            maximum_bytes=(
                131072 if getattr(enrollment, "holder_grants", None) is not None else 65536
            ),
        )
        if any(not isinstance(v, dict) for v in body.values()):
            raise UnauthenticatedError("invalid_request")
        return await run_in_threadpool(read, body)

    from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment

    if isinstance(enrollment, ArenaMonitorReadEnrollment):
        # The Work-only native v2 transport retains its closed parameter shape.
        app.post("/devgraph/native-read/v3/challenges")(challenge)
        app.post("/devgraph/native-read/v3/enrollments")(enroll)

        @app.post("/devgraph/native-read/v3/requests")
        async def arena_request_read(request: Request):
            from devgraph.api.wallet_monitor import wallet_monitor_read

            body = await _body(request, {"credential", "call", "proof"}, maximum_bytes=131072)
            return await run_in_threadpool(
                wallet_monitor_read, storage, enrollment, audit_log, body
            )

    from devgraph.auth.monitor_read import MemberMonitorReadEnrollment

    if isinstance(enrollment, MemberMonitorReadEnrollment):
        from devgraph.api.wallet_monitor import register_wallet_monitor

        register_wallet_monitor(
            app,
            storage=storage,
            enrollment=enrollment,
            audit_log=audit_log,
            web_wallet_url=web_wallet_url,
        )
    return app
