"""Explicit HTTPS-only request routes; no grant/approval/issuer administration."""

import sqlite3

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from devgraph.api.native_read import _body
from devgraph.auth.access_requests import AccessRequestInbox, RequestAction
from devgraph.auth.errors import UnauthenticatedError


def register_access_request_routes(app: FastAPI, inbox: AccessRequestInbox) -> None:
    async def handle(request: Request, operation: str):
        headers = {"Cache-Control": "no-store"}
        try:
            # TLS terminators must use the host's reviewed proxy configuration;
            # arbitrary X-Forwarded-Proto headers do not authorize cleartext use.
            if (
                request.url.scheme != "https"
                or f"https://{request.url.netloc}" != inbox.receiver
                or request.headers.get("authorization")
            ):
                raise UnauthenticatedError("invalid_request")
            if operation == "challenge":
                body = await _body(request, {"request"})
                action = RequestAction.model_validate(body["request"])
                result = await run_in_threadpool(inbox.challenge, action)
            else:
                body = await _body(request, {"challenge", "proof", "signature"})
                result = await run_in_threadpool(
                    inbox.execute, body["challenge"], body["proof"], body["signature"]
                )
            return JSONResponse(result, headers=headers)
        except UnauthenticatedError:
            return JSONResponse(
                {"error": "access_request_denied"}, status_code=401, headers=headers
            )
        except (ValueError, TypeError, RecursionError):
            return JSONResponse(
                {"error": "invalid_access_request"}, status_code=400, headers=headers
            )
        except sqlite3.Error:
            # A lost/failed response cannot announce success. Keep the original
            # request ID and reconcile status; never submit an automatic retry.
            return JSONResponse(
                {"error": "access_request_unavailable"}, status_code=503, headers=headers
            )

    @app.post("/devgraph/access-requests/challenges")
    async def challenge(request: Request):
        return await handle(request, "challenge")

    @app.post("/devgraph/access-requests/actions")
    async def action(request: Request):
        return await handle(request, "action")
