"""Explicit finite-session routes with path-local, cookie-free CORS only."""

from contextlib import nullcontext

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_session_transport import MAX_BYTES, SESSION_BASES, loads


def request_origin(enrollment, origin):
    sessions = getattr(getattr(enrollment, "holder_grants", None), "holder_sessions", None)
    return nullcontext() if sessions is None else sessions.from_origin(origin)


def configured_native_origins(enrollment):
    sessions = getattr(getattr(enrollment, "holder_grants", None), "holder_sessions", None)
    if sessions is None or not sessions.policy.enabled:
        return ()
    return tuple(
        origin for origin in sessions.policy.allowed_origins if origin.startswith("app://")
    )


def _headers(method):
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": method + ", OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
    }


async def _body(request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_BYTES:
            raise ValueError("request too large")
    return loads(raw)


def register_holder_sessions(app, *, host, now=None):
    """Default capabilities are unsupported; a reviewed policy enables exchange."""

    def clock():
        return now() if now is not None else None

    def capabilities(selected_base):
        def endpoint(request: Request):
            sessions = getattr(host, "holder_sessions", None)
            supported = sessions is not None and sessions.policy.enabled
            prefix = request.scope.get("devgraph.canonical_access_prefix", "") + selected_base
            result = {
                "schema": "secs.holder-session-capabilities.v1",
                "supported": supported,
                "profile": "secs.holder-session.v1",
                "issuer_pin": host.pin,
                "audience": "devgraph",
                "grant_schemas": ["secs.holder-grant.v2"],
                "credential_schema": "secs.holder-session-credential.v1",
                "presentation_schema": "secs.holder-session-presentation.v1",
                "read_only": True,
                "max_ttl_seconds": sessions.policy.max_ttl_seconds if supported else 300,
                "review_ttl_seconds": 120,
                "proof_max_ttl_seconds": 60,
                "challenge_path": prefix + "/challenges",
                "exchange_path": prefix + "/exchange",
                "status_path": prefix + "/status",
            }
            return JSONResponse(result, headers=_headers("GET"))

        return endpoint

    for base in SESSION_BASES:
        app.get(base + "/capabilities", include_in_schema=False)(capabilities(base))

        def handler(operation):
            async def invoke(request: Request):
                sessions = getattr(host, "holder_sessions", None)
                if sessions is None or not sessions.policy.enabled:
                    return JSONResponse(
                        {"error": "holder_sessions_not_enabled"},
                        status_code=501,
                        headers=_headers("POST"),
                    )
                try:
                    body = await _body(request)
                    result = await run_in_threadpool(
                        getattr(sessions, operation), body, now=clock()
                    )
                    return JSONResponse(result, headers=_headers("POST"))
                except (ValueError, UnauthenticatedError):
                    return JSONResponse(
                        {"error": "holder_session_denied"},
                        status_code=401,
                        headers=_headers("POST"),
                    )

            return invoke

        for suffix, operation in (
            ("challenges", "prepare"),
            ("exchange", "exchange"),
            ("status", "status"),
        ):
            app.post(base + "/" + suffix, include_in_schema=False)(handler(operation))

        def preflight(method):
            async def options(request: Request):
                requested = request.headers.get("access-control-request-method", method)
                headers = request.headers.get("access-control-request-headers", "content-type")
                if requested != method or headers.lower().strip() not in ("", "content-type"):
                    return Response(status_code=403, headers=_headers(method))
                return Response(status_code=204, headers=_headers(method))

            return options

        for suffix in ("capabilities", "challenges", "exchange", "status"):
            app.options(base + "/" + suffix, include_in_schema=False)(
                preflight("GET" if suffix == "capabilities" else "POST")
            )
