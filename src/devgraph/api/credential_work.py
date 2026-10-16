"""Same-origin browser transport. No cookie, membership or bearer grants a write."""

import asyncio

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from devgraph.api.routes import _envelope, _receipt_envelope
from devgraph.arena_contract import Arena
from devgraph.auth.secs_issue_create import SecSIssueCreateDenied, _strict_json_object
from devgraph.auth.secs_work import SecSWorkDenied


def register_credential_work(app, services):
    from importlib.resources import files

    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets/credential-v2",
        StaticFiles(directory=str(files("devgraph").joinpath("frontend/static/credential-v2"))),
        name="credential-v2-assets",
    )

    async def submit(request, action):
        host = services.credential_work_host
        if host is None:
            raise SecSWorkDenied("credential_transport_unavailable")
        selected = {}
        for name, value in request.scope["headers"]:
            if name in {b"origin", b"authorization", b"content-type", b"sec-fetch-site"}:
                if name in selected or len(value) > 512:
                    raise SecSWorkDenied()
                try:
                    selected[name] = value.decode("ascii", errors="strict")
                except UnicodeDecodeError:
                    raise SecSWorkDenied() from None
        origin = selected.get(b"origin")
        if (
            b"authorization" in selected
            or request.url.query
            or selected.get(b"content-type", "").split(";")[0] != "application/json"
            or selected.get(b"sec-fetch-site", "same-origin") != "same-origin"
            or origin != f"{request.url.scheme}://{request.url.netloc}"
        ):
            raise SecSWorkDenied("browser_origin_denied")
        # Reject unapproved callers before reading payload or starting a child.
        await run_in_threadpool(host.require_origin, origin)

        async def body():
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 262_144:
                    raise SecSWorkDenied("credential_request_too_large")
                raw.extend(chunk)
            return bytes(raw)

        try:
            raw = await asyncio.wait_for(body(), timeout=10)
            value = _strict_json_object(
                raw, maximum_bytes=262_144, reason="invalid_credential_request"
            )
            result = await run_in_threadpool(getattr(host, action), value=value, origin=origin)
        except (SecSIssueCreateDenied, ValueError, TypeError, KeyError, TimeoutError):
            raise SecSWorkDenied("invalid_credential_request") from None
        if action == "provider_profile":
            from fastapi.responses import JSONResponse

            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        if action in {"prepare", "capabilities"}:
            return result
        if action == "status":
            if result is None:
                return {"state": "unknown"}
            return {"state": "committed", "receipt": _receipt_envelope(result, duplicate=True)}
        work, receipt, duplicate = result
        from devgraph.named_requests import parse_named_request
        from devgraph.todo_views import todo_summary
        from devgraph.work_requests import WorkRequest

        parsed = parse_named_request(value["request"].encode("utf-8"))
        if isinstance(parsed, WorkRequest) and parsed.version == 2:
            return {
                "state": "committed",
                "schema": "devgraph.work-result.v2",
                "receipt": _receipt_envelope(receipt, duplicate=duplicate),
                "work": None if work is None else todo_summary(work, description=True),
            }
        return {
            "state": "committed",
            "receipt": _receipt_envelope(receipt, duplicate=duplicate),
            "work": None if work is None or isinstance(work, Arena) else _envelope(work),
            "arena": work if isinstance(work, Arena) else None,
        }

    @app.post("/credential-work/v2/prepare")
    async def prepare(request: Request):
        return await submit(request, "prepare")

    @app.post("/credential-work/v2/execute")
    async def execute(request: Request):
        return await submit(request, "execute")

    @app.post("/credential-work/v2/status")
    async def status(request: Request):
        return await submit(request, "status")

    @app.post("/credential-work/v2/capabilities")
    async def capabilities(request: Request):
        return await submit(request, "capabilities")

    @app.post("/credential-work/v2/provider-profile")
    async def provider_profile(request: Request):
        return await submit(request, "provider_profile")
