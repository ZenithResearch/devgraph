"""Owner-admitted service routes over one canonical authorization composition.

The legacy routes and their origin-bound pending actions are untouched. This
adapter changes transport routing only; the canonical app still verifies all
Wallet proofs, policies, revocation, replay and operation permissions itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

PREFIX = "/service/v1"
METADATA = "/.well-known/devgraph-service.json"


def access_point_binding(host, *, origin, authority_receiver):
    current = int(datetime.now(timezone.utc).timestamp())
    claims = dict(
        schema="secs.service-access-point.v1",
        issuer=host.pin["issuer"],
        key_id=host.pin["key_id"],
        service=host.service,
        origin=origin,
        authority_receiver=authority_receiver,
        prefix=PREFIX,
        not_before=max(0, current - 30),
        not_after=current + 86400,
    )
    return host.invoke("issue_access_point", claims=claims, now=current)


class CanonicalServiceRoute:
    def __init__(self, app, *, origin, authority_receiver):
        self.app, self.origin = app, origin
        self.authority = urlsplit(authority_receiver)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await JSONResponse({"error": "unsupported_service_transport"}, status_code=400)(
                scope, receive, send
            )
            return
        hosts = [v.decode("latin1") for k, v in scope["headers"] if k.lower() == b"host"]
        host = hosts[0] if len(hosts) == 1 else ""
        if scope["scheme"] != "https" or "https://" + host != self.origin:
            await JSONResponse({"error": "service_route_mismatch"}, status_code=421)(
                scope, receive, send
            )
            return
        # Mounting cannot expose arbitrary host routes or an arbitrary destination.
        path = scope["path"]
        if not path.startswith(PREFIX + "/"):
            await JSONResponse({"error": "invalid_service_path"}, status_code=400)(
                scope, receive, send
            )
            return
        selected = dict(scope)
        selected["path"] = path[len(PREFIX) :]
        selected["raw_path"] = selected["path"].encode("utf8")
        selected["root_path"] = ""
        selected["scheme"] = "https"
        selected["server"] = (self.authority.hostname, self.authority.port or 443)
        selected["headers"] = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower() not in (b"host", b"forwarded", b"x-forwarded-host", b"x-forwarded-proto")
        ]
        selected["headers"].append((b"host", self.authority.netloc.encode("ascii")))
        await self.app(selected, receive, send)


def register_service_routes(app, *, canonical_app, host, origin):
    authority_receiver = canonical_app.state.service_authority_receiver
    route = CanonicalServiceRoute(
        canonical_app, origin=origin, authority_receiver=authority_receiver
    )

    @app.get(METADATA)
    async def metadata(request: Request):
        if request.url.scheme != "https" or "https://" + request.url.netloc != origin:
            return JSONResponse({"error": "service_route_mismatch"}, status_code=421)
        try:
            binding = await run_in_threadpool(
                access_point_binding, host, origin=origin, authority_receiver=authority_receiver
            )
        except (ValueError, OSError):
            return JSONResponse({"error": "service_binding_unavailable"}, status_code=503)
        return JSONResponse(
            dict(schema="devgraph.service-access-point.v1", binding=binding),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    app.mount(PREFIX, route)
