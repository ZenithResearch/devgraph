"""Bounded transport for the authenticated, compiler-owned Cypher read surface."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from devgraph.api.app import ApiServices
from devgraph.api.routes import _credential
from devgraph.auth.enforcement import require_scope
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.scopes import CATEGORY_READ
from devgraph.cypher_read import MAX_REQUEST_BYTES, CypherReadError


def register_cypher_read(app: FastAPI, services: ApiServices) -> None:
    @app.exception_handler(CypherReadError)
    def cypher_error(request: Request, exc: CypherReadError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": "about:blank", "title": "Cypher read failed",
                "status": exc.status, "detail": exc.code,
            },
            media_type="application/problem+json",
        )

    @app.post("/query/cypher", name="cypher_read_v1")
    async def execute(request: Request):
        selected = {}
        for name, value in request.scope["headers"]:
            name = name.lower()
            if name not in {b"authorization", b"content-type", b"content-encoding"}:
                continue
            if name in selected or len(value) > 4096:
                raise UnauthenticatedError("invalid read request headers")
            try:
                selected[name] = value.decode("ascii")
            except UnicodeError:
                raise UnauthenticatedError("invalid read request headers") from None
        credential = _credential(selected.get(b"authorization"))
        # Authenticate before accepting a body; the service verifies once more
        # immediately before execution so expiry during upload stays fail-closed.
        context = await run_in_threadpool(
            services.verifier.verify, credential, audience=services.audience,
        )
        require_scope(context, CATEGORY_READ)
        if (
            request.scope.get("query_string") or b"content-encoding" in selected
            or selected.get(b"content-type", "").lower().split(";")[0].strip()
            != "application/json"
        ):
            raise CypherReadError("invalid_cypher_read_transport")
        if services.cypher_read is None:
            raise CypherReadError("cypher_backend_unavailable", 503)

        async def body():
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                    raise CypherReadError("cypher_request_too_large", 413)
                raw.extend(chunk)
            return bytes(raw)

        try:
            raw = await asyncio.wait_for(body(), timeout=5)
            return await asyncio.wait_for(
                run_in_threadpool(
                    services.cypher_read.execute, credential=credential, request_json=raw,
                ),
                timeout=8,
            )
        except (asyncio.TimeoutError, TimeoutError):
            # A stalled worker keeps its runner slot until its transaction exits;
            # returning HTTP 504 cannot make room for unbounded orphan queries.
            raise CypherReadError("cypher_query_timeout", 504) from None
