"""Retain request attempts/results without reading any request or response content."""

from __future__ import annotations

import uuid

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from devgraph.ops.retained_audit import AuditUnavailable, RetainedAuditLog


class RetainedAuditMiddleware:
    def __init__(self, app, audit_log: RetainedAuditLog):
        self.app, self.audit_log = app, audit_log

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in {"/live", "/ready"}:
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        started = False

        async def event(phase, status=None):
            route = scope.get("route")
            await run_in_threadpool(
                self.audit_log.request_event,
                request_id=request_id,
                method=scope["method"],
                phase=phase,
                route=getattr(route, "name", "unmatched"),
                status=status,
            )

        async def audited_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                await event("response", message["status"])
                started = True
            await send(message)

        try:
            await event("attempt")  # Fail before any mutation if audit is unavailable.
            await self.app(scope, receive, audited_send)
        except AuditUnavailable:
            if started:
                raise
            response = JSONResponse(
                status_code=503,
                content={
                    "title": "Retained audit unavailable",
                    "status": 503,
                    "detail": "Retry mutations with the same idempotency key; a commit may exist.",
                },
            )
            await response(scope, receive, send)
        except Exception:
            if not started:
                await event("response", 500)
            raise
