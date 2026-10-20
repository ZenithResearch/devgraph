"""Explicit HTTPS-only request routes; no grant/approval/issuer administration."""

import sqlite3

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from devgraph.api.native_read import _body
from devgraph.auth.access_requests import AccessRequestInbox
from devgraph.auth.errors import UnauthenticatedError
from devgraph.storage.base import StorageUnavailable


def register_access_request_routes(app: FastAPI, inbox: AccessRequestInbox) -> None:
    app.state.access_requests_enabled = True
    if hasattr(inbox, "public_catalog"):
        app.state.access_catalog_enabled = True

        @app.get("/devgraph/access-requests/v2/catalog")
        async def catalog(request: Request):
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            if (
                request.url.scheme != "https"
                or f"https://{request.url.netloc}" != inbox.receiver
                or request.headers.get("authorization")
                or request.query_params
            ):
                return JSONResponse(
                    {"error": "invalid_access_catalog"}, status_code=400, headers=headers
                )
            try:
                value = await run_in_threadpool(inbox.public_catalog)
                from devgraph.auth.delegated_contract import canonical_json

                if len(canonical_json(value)) > 262144:
                    raise StorageUnavailable("catalog capacity exceeded")
                return JSONResponse(value, headers=headers)
            except UnauthenticatedError:
                return JSONResponse(
                    {"error": "access_catalog_unavailable"}, status_code=401, headers=headers
                )
            except (ValueError, StorageUnavailable, OSError):
                return JSONResponse(
                    {"error": "access_catalog_unavailable"}, status_code=503, headers=headers
                )

    async def handle(request: Request, operation: str, wallet=False):
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
                action = inbox.parse_request(body["request"])
                result = await run_in_threadpool(
                    inbox.wallet_challenge if wallet else inbox.challenge, action
                )
            else:
                body = await _body(request, {"challenge", "proof", "signature"})
                result = await run_in_threadpool(
                    inbox.execute_wallet if wallet else inbox.execute,
                    body["challenge"],
                    body["proof"],
                    body["signature"],
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

    @app.post("/devgraph/access-requests/v2/challenges")
    async def wallet_challenge(request: Request):
        return await handle(request, "challenge", wallet=True)

    @app.post("/devgraph/access-requests/v2/actions")
    async def wallet_action(request: Request):
        return await handle(request, "action", wallet=True)
