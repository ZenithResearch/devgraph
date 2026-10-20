"""Opt-in monitor inbox. Every data/action call has signed operator approval."""

import sqlite3
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from devgraph.api.native_read import _body
from devgraph.auth.access_operator import OperatorAction
from devgraph.auth.errors import UnauthenticatedError
from devgraph.frontend.wallet import WALLET_CONNECTION_HTML

BASE = "/devgraph/access-operator/v1"


def register_access_operator(app: FastAPI, operator):
    app.state.access_operator_enabled = True

    async def handle(request, challenge=False):
        headers = {"Cache-Control": "no-store"}
        try:
            if (
                request.url.scheme != "https"
                or f"https://{request.url.netloc}" != operator.inbox.receiver
                or request.headers.get("authorization")
            ):
                raise UnauthenticatedError("operator_required")
            if challenge:
                body = await _body(request, {"subject_public_key", "installation_id", "action"})
                result = await run_in_threadpool(
                    operator.challenge,
                    body["subject_public_key"],
                    body["installation_id"],
                    OperatorAction.model_validate(body["action"]),
                )
            else:
                body = await _body(request, {"challenge", "signature"})
                result = await run_in_threadpool(
                    operator.execute, body["challenge"], body["signature"]
                )
            return JSONResponse(result, headers=headers)
        except UnauthenticatedError as error:
            if not getattr(error, "_access_operator_audited", False):
                try:
                    await run_in_threadpool(operator.record_denial, "http_request_rejected")
                except sqlite3.Error:
                    return JSONResponse(
                        {"error": "operator_result_unknown"}, status_code=503, headers=headers
                    )
            if str(error) in ("request_changed", "assignment_changed"):
                return JSONResponse(
                    {"error": "operator_" + str(error)}, status_code=409, headers=headers
                )
            if str(error) in (
                "write_activation_unavailable",
                "write_activation_upgrade_required",
                "mixed_activation_unavailable",
                "native_admission_required",
                "activation_unavailable",
                "activation_upgrade_required",
            ):
                return JSONResponse({"error": str(error)}, status_code=400, headers=headers)
            return JSONResponse(
                {"error": "operator_access_denied"}, status_code=401, headers=headers
            )
        except (ValueError, TypeError, RecursionError) as error:
            if not getattr(error, "_access_operator_audited", False):
                try:
                    await run_in_threadpool(operator.record_denial, "invalid_http_selection")
                except sqlite3.Error:
                    return JSONResponse(
                        {"error": "operator_result_unknown"}, status_code=503, headers=headers
                    )
            return JSONResponse(
                {"error": "invalid_operator_selection"}, status_code=400, headers=headers
            )
        except sqlite3.Error:
            return JSONResponse(
                {"error": "operator_result_unknown"}, status_code=503, headers=headers
            )

    @app.post(BASE + "/challenges")
    async def challenge(request: Request):
        return await handle(request, challenge=True)

    @app.post(BASE + "/actions")
    async def action(request: Request):
        return await handle(request)

    @app.get("/monitor/access", include_in_schema=False)
    @app.get("/monitor/access/", include_in_schema=False)
    def page():
        raw = (
            files("devgraph.frontend")
            .joinpath("static/access/index.html")
            .read_text()
            .replace("__WALLET_CONNECTION__", WALLET_CONNECTION_HTML)
        )
        return HTMLResponse(
            raw,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
                ),
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/monitor/access-assets/{name}", include_in_schema=False)
    def asset(name: str):
        if name not in ("app.mjs", "core.mjs", "client.mjs", "policy.mjs", "style.css"):
            return Response(status_code=404)
        return Response(
            files("devgraph.frontend").joinpath("static/access", name).read_bytes(),
            media_type="text/css" if name.endswith(".css") else "text/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
