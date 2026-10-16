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
from devgraph.auth.sdk_receiver_profile import RECEIVER_PROFILE_HEADER
from devgraph.auth.secs_issue_create import _LOWER_HEX_64
from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.work_requests import InvalidWorkRequest, WorkRequest

AUTHORITY_HEADER = "X-Devgraph-Work-Authority"


def register_named_work(app: FastAPI, services: ApiServices) -> None:
    async def submit(
        request: Request,
        response: Response,
        *,
        arena: bool,
        version: int = 1,
        require_profile: bool = False,
        authority_version: int | None = 1,
        status_only: bool = False,
    ):
        selected = {}
        relevant = {b"authorization", b"idempotency-key", b"x-devgraph-work-authority"}
        profile_header = RECEIVER_PROFILE_HEADER.lower().encode("ascii")
        if require_profile:
            relevant.add(profile_header)
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
        profile = selected.get(profile_header, "")
        if require_profile and _LOWER_HEX_64.fullmatch(profile) is None:
            raise SecSWorkDenied("sdk_receiver_profile_required")
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
            parsed = (ArenaRequest if arena else WorkRequest).from_json(raw)
            if not arena and parsed.version != version:
                raise InvalidWorkRequest("invalid_named_request_domain")
        except (InvalidArenaRequest, InvalidWorkRequest):
            raise SecSWorkDenied("invalid_named_request_domain") from None
        from devgraph.auth.secs_issue_create import _strict_json_object

        proof_fields = _strict_json_object(
            projection, maximum_bytes=16_384, reason="invalid_authority_projection"
        )
        proof_version = {
            "secs-devgraph-work-authority.v1": 1,
            "secs-devgraph-work-authority.v2": 2,
        }.get(proof_fields.get("schema"))
        if proof_version is None or (
            authority_version is not None and proof_version != authority_version
        ):
            raise SecSWorkDenied("invalid_authority_version")
        receiver = services.named_work_v2 if proof_version == 2 else services.named_work
        if receiver is None:
            raise SecSWorkDenied("named_work_receiver_unavailable")
        arguments = dict(request_json=raw, projection_json=projection, idempotency_key=key)
        if status_only:
            if proof_version != 2:
                raise SecSWorkDenied("unsupported_status_version")
            receipt = await run_in_threadpool(receiver.status, **arguments)
            if receipt is None:
                return {"state": "unknown"}
            return {"state": "committed", "receipt": _receipt_envelope(receipt, duplicate=True)}
        if require_profile:
            arguments["receiver_profile"] = profile
        work, receipt, duplicate = await run_in_threadpool(receiver.execute, **arguments)
        response.status_code = (
            201
            if receipt.operation
            in ("devgraph.work.create.v1", "devgraph.arena.create.v1", "devgraph.work.create.v2")
            and not duplicate
            else 200
        )
        if version == 2:
            from devgraph.todo_views import todo_summary

            return {
                "schema": "devgraph.work-result.v2",
                "work": None if work is None else todo_summary(work, description=True),
                "receipt": _receipt_envelope(receipt, duplicate=duplicate),
            }
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

    @app.post("/todo-operations/v2")
    async def execute_v2(request: Request, response: Response):
        return await submit(request, response, arena=False, version=2, authority_version=None)

    @app.post("/arena-operations/v1", response_model=ArenaMutationResultEnvelope)
    async def execute_arena(request: Request, response: Response):
        return await submit(request, response, arena=True)

    @app.post("/sdk/work-operations/v1", response_model=MutationResultEnvelope)
    async def execute_sdk(request: Request, response: Response):
        return await submit(request, response, arena=False, require_profile=True)

    @app.post("/sdk/todo-operations/v2")
    async def execute_sdk_v2(request: Request, response: Response):
        return await submit(request, response, arena=False, version=2, require_profile=True)

    @app.post("/todo-operations/v2/status")
    async def todo_status(request: Request, response: Response):
        return await submit(
            request, response, arena=False, version=2, authority_version=2, status_only=True
        )

    @app.post("/work-operations/v2", response_model=MutationResultEnvelope)
    async def credential_work(request: Request, response: Response):
        return await submit(request, response, arena=False, authority_version=2)

    @app.post("/arena-operations/v2", response_model=ArenaMutationResultEnvelope)
    async def credential_arena(request: Request, response: Response):
        return await submit(request, response, arena=True, authority_version=2)

    @app.post("/work-operations/v2/status")
    async def work_status(request: Request, response: Response):
        return await submit(request, response, arena=False, authority_version=2, status_only=True)

    @app.post("/arena-operations/v2/status")
    async def arena_status(request: Request, response: Response):
        return await submit(request, response, arena=True, authority_version=2, status_only=True)
