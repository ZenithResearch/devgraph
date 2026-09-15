"""Bounded HTTP transport for signed, closed named Work requests."""

from __future__ import annotations

import asyncio
import base64
import binascii

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool

from devgraph.api.app import ApiServices
from devgraph.api.routes import _envelope, _receipt_envelope
from devgraph.api.schemas import ArenaMutationResultEnvelope, MutationResultEnvelope
from devgraph.arena_contract import Arena
from devgraph.arena_requests import ArenaRequest, InvalidArenaRequest
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.work_requests import InvalidWorkRequest, WorkRequest

AUTHORITY_HEADER = "X-Devgraph-Work-Authority"


def register_named_work(app: FastAPI, services: ApiServices) -> None:
    async def submit(request: Request, response: Response, *, arena: bool):
        selected = {}
        relevant = {b"authorization", b"idempotency-key", b"x-devgraph-work-authority"}
        for name, value in request.scope["headers"]:
            name = name.lower()
            if name not in relevant:
                continue
            if name in selected or len(value) > 21_846:
                raise SecSWorkDenied()
            try:
                selected[name] = value.decode("ascii")
            except UnicodeDecodeError:
                raise SecSWorkDenied() from None
        if b"authorization" in selected or request.scope.get("query_string"):
            raise SecSWorkDenied()
        if services.named_work is None:
            raise SecSWorkDenied("named_work_receiver_unavailable")
        proof = selected.get(b"x-devgraph-work-authority", "")
        key = selected.get(b"idempotency-key", "")
        if not proof or not key or len(key) > 128:
            raise SecSWorkDenied()
        try:
            projection = base64.b64decode(
                proof + "=" * (-len(proof) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, binascii.Error):
            raise SecSWorkDenied() from None
        if (
            len(projection) > 16_384
            or base64.urlsafe_b64encode(projection).rstrip(b"=").decode("ascii") != proof
        ):
            raise SecSWorkDenied()

        async def bounded_body():
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 131_072:
                    raise SecSWorkDenied("named_work_request_too_large")
                raw.extend(chunk)
            return bytes(raw)

        try:
            raw = await asyncio.wait_for(bounded_body(), timeout=10)
        except (asyncio.TimeoutError, TimeoutError):
            raise SecSWorkDenied("named_work_request_timeout") from None
        try:
            (ArenaRequest if arena else WorkRequest).from_json(raw)
        except (InvalidArenaRequest, InvalidWorkRequest):
            raise SecSWorkDenied("invalid_named_request_domain") from None
        work, receipt, duplicate = await run_in_threadpool(
            services.named_work.execute,
            request_json=raw,
            projection_json=projection,
            idempotency_key=key,
        )
        response.status_code = (
            201 if receipt.operation in ("devgraph.work.create.v1", "devgraph.arena.create.v1")
            and not duplicate else 200
        )
        if arena:
            return ArenaMutationResultEnvelope(
                arena=work if isinstance(work, Arena) else None,
                work=None if work is None or isinstance(work, Arena) else _envelope(work),
                receipt=_receipt_envelope(receipt, duplicate=duplicate),
            )
        return MutationResultEnvelope(
            work=None if work is None else _envelope(work),
            receipt=_receipt_envelope(receipt, duplicate=duplicate),
        )

    @app.post("/work-operations/v1", response_model=MutationResultEnvelope)
    async def execute(request: Request, response: Response):
        return await submit(request, response, arena=False)

    @app.post("/arena-operations/v1", response_model=ArenaMutationResultEnvelope)
    async def execute_arena(request: Request, response: Response):
        return await submit(request, response, arena=True)
