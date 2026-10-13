"""Explicit composition of native enrollment with the existing bounded read API.

No environment variable enables this on the live host. The operator supplies
storage, policy, trusted membership verification and a private enrollment DB.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from devgraph.api.delegated_read import register_delegated_read
from devgraph.auth.delegated_read import DelegatedReadService
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.auth.native_read import NativeReadEnrollment
from devgraph.storage.base import GraphStorage


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_field")
        value[key] = item
    return value


async def _body(request: Request, fields: set[str]) -> dict:
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 65536:
            raise UnauthenticatedError("invalid_request")
    try:
        value = json.loads(
            data,
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise UnauthenticatedError("invalid_request") from None


def create_native_read_app(
    *, storage: GraphStorage, enrollment: NativeReadEnrollment, audit_log: AuditLog
) -> FastAPI:
    app = FastAPI(title="Devgraph native read preview", docs_url=None, redoc_url=None)

    @app.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse(
            {"error": "access_denied"}, status_code=401, headers={"Cache-Control": "no-store"}
        )

    @app.exception_handler(KeyError)
    async def missing(_request, _error):
        return JSONResponse(
            {"error": "not_found"}, status_code=404, headers={"Cache-Control": "no-store"}
        )

    @app.exception_handler(ForbiddenError)
    async def forbidden(_request, _error):
        return JSONResponse(
            {"error": "read_not_permitted"}, status_code=403, headers={"Cache-Control": "no-store"}
        )

    @app.post("/devgraph/native-read/challenges")
    async def challenge(request: Request):
        body = await _body(request, {"subject_public_key", "installation_id"})
        result = await run_in_threadpool(
            enrollment.challenge, body["subject_public_key"], body["installation_id"]
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/devgraph/native-read/enrollments")
    async def enroll(request: Request):
        body = await _body(request, {"challenge", "proof", "signature"})
        result = await run_in_threadpool(
            enrollment.enroll, body["challenge"], body["proof"], body["signature"]
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    register_delegated_read(
        app,
        DelegatedReadService(
            storage=storage, verifier=enrollment, audience="devgraph", audit_log=audit_log
        ),
    )
    return app
