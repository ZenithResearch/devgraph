"""Public monitor shell with holder-signed, allowlisted read transport only."""

import re
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from devgraph.api.native_read import _body
from devgraph.api.topology import register_topology_assets
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.auth.monitor_read import monitor_target, project_monitor_read
from devgraph.auth.native_read import RESOURCE
from devgraph.auth.resource_membership import ResourceMembershipAdmission
from devgraph.frontend.app import FRONTEND_HTML
from devgraph.frontend.wallet import WALLET_CONNECTION_HTML
from devgraph.kanban import BoardChanged, InvalidBoardFilter
from devgraph.storage.base import StorageUnavailable
from devgraph.topology import InvalidTopologyFilter

BASE = "/monitor/wallet-access/v1"


def wallet_monitor_read(storage, enrollment, audit_log, body):
    if any(not isinstance(body[k], dict) for k in ("call", "credential", "proof")):
        raise UnauthenticatedError("invalid_request")
    call = body["call"]
    if (
        set(call) != {"operation", "resource", "parameters"}
        or call["resource"] != RESOURCE
        or not isinstance(call["parameters"], dict)
        or set(call["parameters"]) != {"path"}
    ):
        raise UnauthenticatedError("unsupported_read")
    try:
        operation, _, _ = monitor_target(call["parameters"]["path"])
    except (ValueError, TypeError):
        raise UnauthenticatedError("unsupported_read") from None
    expected = (
        "devgraph." + operation if operation.startswith("arena.") else "devgraph.work." + operation
    )
    if call["operation"] != expected:
        raise UnauthenticatedError("unsupported_read")
    context = enrollment.verify_request(**body)
    try:
        from devgraph.auth.selector_grants import SelectorGrant, materialize

        if any(isinstance(g, SelectorGrant) for g in context.read_grants):
            with (
                storage.work_mutation_transaction(),
                enrollment.holder_grants.current_read(body["credential"], body["proof"]),
            ):
                grants = materialize(storage, context.read_grants)
                result = project_monitor_read(storage, grants, call["parameters"]["path"])
        else:
            result = project_monitor_read(storage, context.read_grants, call["parameters"]["path"])
    except (InvalidTopologyFilter, InvalidBoardFilter, ValueError):
        return JSONResponse({"error": "invalid_read_filter"}, status_code=400)
    audit_log.record(
        actor_id=context.actor_id,
        session_id=context.session_id,
        correlation_id=context.correlation_id,
        category="delegated.read",
        operation="wallet_monitor_read",
        safe_summary={},
    )
    return result


def register_wallet_monitor(app: FastAPI, *, storage, enrollment, audit_log, web_wallet_url=None):
    register_topology_assets(app)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/monitor", response_class=HTMLResponse, include_in_schema=False)
    def shell():
        return HTMLResponse(
            FRONTEND_HTML.replace(
                '<a href="/monitor/selection">', '<a hidden href="/monitor/selection">'
            ).replace(
                "</nav>",
                '<a href="/monitor/access/">Access requests</a></nav>'
                if getattr(app.state, "access_operator_enabled", False)
                else "</nav>",
            ),
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/monitor/kanban", include_in_schema=False)
    @app.get("/monitor/kanban/", include_in_schema=False)
    def board_shell():
        raw = files("devgraph.frontend").joinpath("static/kanban/index.html").read_text()
        raw = re.sub(
            r'<form id="connect".*?</form>',
            '<section id="connect">'
            + WALLET_CONNECTION_HTML
            + '<button id="refresh" type="button">Refresh</button></section>',
            raw,
            flags=re.S,
        )
        raw = raw.replace('<a href="/monitor/selection">', '<a hidden href="/monitor/selection">')
        raw = raw.replace('<div class="connection">', '<div class="connection" hidden>')
        raw = raw.replace(
            "</head>",
            '<link rel="stylesheet" href="/monitor/topology-assets/wallet-access.css">'
            '<script src="/monitor/topology-assets/wallet-access.js"></script></head>',
        )
        return HTMLResponse(
            raw,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/assets/kanban/{name}", include_in_schema=False)
    @app.get("/monitor/kanban-assets/{name}", include_in_schema=False)
    def board_asset(name: str):
        if name not in ("app.mjs", "core.mjs", "wallet.mjs", "style.css"):
            return Response(status_code=404)
        return Response(
            files("devgraph.frontend").joinpath("static/kanban", name).read_bytes(),
            media_type="text/css" if name.endswith(".css") else "text/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/assets/credential-v2/{name}", include_in_schema=False)
    def read_setup_asset(name: str):
        if name not in (
            "provider-setup.mjs",
            "service-route.mjs",
            "result.mjs",
            "subject.mjs",
            "wallet-errors.mjs",
            "read-access.mjs",
            "access-request.mjs",
            "scope-picker.mjs",
            "web-wallet.mjs",
        ):
            return Response(status_code=404)
        return Response(
            files("devgraph.frontend").joinpath("static/credential-v2", name).read_bytes(),
            media_type="text/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get(BASE, include_in_schema=False)
    def configuration(request: Request):
        # Public trust proposal, never an automatic trust installation or grant.
        origin = f"{request.url.scheme}://{request.url.netloc}"
        authority = enrollment._authority_policy(enrollment.policy())
        pin = {key: authority[key] for key in ("issuer", "key_id", "public_key", "audience")}
        pin["callers"] = [{"kind": "browser", "id": origin}]
        return {
            "schema": "devgraph.wallet-read-configuration.v1",
            "endpoint": BASE,
            **(
                {"service_access_point_endpoint": "/.well-known/devgraph-service.json"}
                if getattr(app.state, "canonical_service_app", None) is not None
                else {}
            ),
            # Explicit resource admission disclosure. A missing field on older
            # services retains their clients' previous membership-proof behavior.
            "membership_requirement": "optional"
            if isinstance(enrollment.membership_check, ResourceMembershipAdmission)
            and not enrollment.membership_check.required
            else "required",
            **(
                {
                    "holder_grants": {
                        "schema": enrollment.holder_grants.grant_schema,
                        "service": enrollment.holder_grants.service,
                        "endpoint": BASE + "/grants",
                    }
                }
                if getattr(enrollment, "holder_grants", None) is not None
                else {}
            ),
            **(
                {"access_catalog_endpoint": "/devgraph/access-requests/v2/catalog"}
                if getattr(app.state, "access_catalog_enabled", False)
                else {}
            ),
            "access_requests_endpoint": "/devgraph/access-requests/v2"
            if getattr(app.state, "access_requests_enabled", False)
            else None,
            "web_wallet_url": web_wallet_url,
            "provider_profile": {
                "schema": "castalia.provider-profile.v1",
                "display_name": "Devgraph read access",
                "origins": [origin],
                "membership": None,
                "presentations": [pin],
            },
        }

    @app.post(BASE + "/challenges")
    async def challenge(request: Request):
        body = await _body(request, {"subject_public_key", "installation_id"})
        return await run_in_threadpool(
            enrollment.challenge, body["subject_public_key"], body["installation_id"]
        )

    @app.post(BASE + "/enrollments")
    async def enroll(request: Request):
        body = await _body(request, {"challenge", "proof", "signature"})
        return await run_in_threadpool(enrollment.enroll, **body)

    if getattr(enrollment, "holder_grants", None) is not None:

        @app.post(BASE + "/grants")
        async def holder_grant(request: Request):
            body = await _body(request, {"challenge", "proof", "signature"})
            return await run_in_threadpool(enrollment.holder_grants.issue, **body)

    def read(body):
        return wallet_monitor_read(storage, enrollment, audit_log, body)

    @app.post(BASE + "/requests")
    async def request_read(request: Request):
        body = await _body(
            request,
            {"credential", "call", "proof"},
            maximum_bytes=(
                131072 if getattr(enrollment, "holder_grants", None) is not None else 65536
            ),
        )
        try:
            from devgraph.api.holder_sessions import request_origin

            with request_origin(enrollment, request.headers.get("origin")):
                return await run_in_threadpool(read, body)
        except (UnauthenticatedError, ForbiddenError):
            audit_log.record(
                actor_id="unverified",
                session_id="none",
                correlation_id="monitor-read",
                category="delegated.read.denied",
                operation="wallet_monitor_read_denied",
                safe_summary={},
            )
            raise
        except StorageUnavailable:
            return JSONResponse({"error": "scoped_read_unavailable"}, status_code=503)
        except BoardChanged:
            return JSONResponse({"error": "view_changed"}, status_code=409)
