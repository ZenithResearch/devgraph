"""Bounded public read/member-request pilot; no public operator administration.

Use TLS on the tunnel's upstream connection too. Forwarded headers are ignored.
This module creates no issuer, trust, assignment, database or synthetic authority.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from anyio import fail_after
from starlette.responses import JSONResponse

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.holder_session_transport import (
    SESSION_GET_PATHS,
    SESSION_PATHS,
    SESSION_POST_PATHS,
)
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json

POST_PATHS = frozenset(
    base + suffix
    for base, suffixes in (
        ("/devgraph/native-read/v2", ("/challenges", "/enrollments", "/grants", "/requests")),
        ("/monitor/wallet-access/v1", ("/challenges", "/enrollments", "/grants", "/requests")),
        ("/devgraph/access-requests/v2", ("/challenges", "/actions")),
    )
    for suffix in suffixes
)
GET_PATHS = frozenset(
    (
        "/",
        "/monitor",
        "/monitor/kanban",
        "/monitor/kanban/",
        "/monitor/wallet-access/v1",
        "/.well-known/devgraph-connection.json",
        "/live",
    )
)
ASSET = re.compile(
    r"^/(?:monitor/(?:topology-assets|kanban-assets)|assets/(?:kanban|credential-v2))/"
    r"[a-zA-Z0-9_.-]{1,80}$"
)


OPERATOR_POST_PATHS = frozenset(
    ("/devgraph/access-operator/v1/challenges", "/devgraph/access-operator/v1/actions")
)
OPERATOR_GET_PATHS = frozenset(("/monitor/access", "/monitor/access/"))
OPERATOR_ASSET = re.compile(r"^/monitor/access-assets/[a-zA-Z0-9_.-]{1,80}$")


def public_origin(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
        or value != "https://" + url.netloc
        or url.netloc != url.netloc.lower()
        or url.hostname.endswith(".")
    ):
        raise ValueError("canonical HTTPS public origin required")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("invalid public port")
    return value


class PublicReadBoundary:
    """Exact TLS/Host and closed routes, with global abuse/resource bounds.

    A global budget avoids trusting spoofable forwarded source-IP headers.
    Holder proof, membership, replay and scope verification remain downstream.
    """

    get_paths = GET_PATHS
    post_paths = POST_PATHS
    asset_patterns = (ASSET,)
    concurrency_limit = 8
    burst_capacity = 60.0
    refill_per_second = 2

    def body_limit(self, path, method):
        if method == "POST" and path in SESSION_POST_PATHS:
            return 131072
        if method == "POST" and path in OPERATOR_POST_PATHS:
            return 524288
        if method == "POST" and path in (
            "/monitor/wallet-access/v1/requests",
            "/devgraph/native-read/v2/requests",
        ):
            return 131072
        return 65536 if method == "POST" else 0

    def __init__(
        self,
        app,
        *,
        origin: str,
        expires_at: int,
        clock=time.time,
        approved_operator_policy=None,
        canonical_paths=False,
    ):
        self.canonical_paths = canonical_paths
        self.app, self.origin = app, public_origin(origin)
        self.host = urlsplit(origin).netloc.encode("ascii")
        self.expires_at, self.clock = expires_at, clock
        self.approved_operator_policy = approved_operator_policy
        self.active = 0
        self.tokens, self.last = self.burst_capacity, time.monotonic()

    def expired(self):
        return self.clock() >= self.expires_at

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)

        async def reject(code, message):
            await JSONResponse(
                {"error": message},
                status_code=code,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )(scope, receive, send)

        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers", [])
        path, method = scope["path"], scope["method"]
        if self.canonical_paths and path.startswith("/service/v1/"):
            path = path[len("/service/v1") :]
        session_route = (
            method == "GET" and path in SESSION_GET_PATHS
            or method == "POST" and path in SESSION_POST_PATHS
            or method == "OPTIONS" and path in SESSION_PATHS
        )
        hosts = [v for k, v in headers if k.lower() == b"host"]
        origins = [v for k, v in headers if k.lower() == b"origin"]
        if (
            scope.get("scheme") != "https"
            or hosts != [self.host]
            or len(origins) > 1
            or origins and not session_route and origins != [self.origin.encode()]
            or any(k.lower() == b"authorization" for k, _ in headers)
        ):
            return await reject(421, "invalid_public_receiver")
        if self.expired():
            return await reject(503, "pilot_window_closed")
        # Apply the SAME route, method, body and abuse limits inside the opt-in
        # canonical namespace. Never forward an unrestricted prefixed path.

        operator_route = (method == "POST" and path in OPERATOR_POST_PATHS) or (
            method == "GET" and (path in OPERATOR_GET_PATHS or OPERATOR_ASSET.fullmatch(path))
        )
        operator_allowed = False
        if operator_route and self.approved_operator_policy is not None:
            # No implicit public administration. Deployment must separately pin
            # the explicitly approved read-only operator policy; changes close it.
            if not self.approved_operator_policy():
                return await reject(503, "operator_policy_changed")
            operator_allowed = True
        if not (
            (
                method == "GET"
                and (
                    path in self.get_paths
                    or any(p.fullmatch(path) for p in self.asset_patterns)
                    or self.canonical_paths
                    and path == "/.well-known/devgraph-service.json"
                )
            )
            or (method == "POST" and path in self.post_paths)
            or operator_allowed
            or session_route
        ):
            return await reject(404, "not_found")
        if (
            sum(len(k) + len(v) for k, v in headers) > 16384
            or len(scope.get("query_string", b"")) > 2048
            or b"%" in scope.get("raw_path", b"")
            or method == "POST"
            and scope.get("query_string")
        ):
            return await reject(400, "invalid_request")
        now = time.monotonic()
        self.tokens = min(
            self.burst_capacity, self.tokens + (now - self.last) * self.refill_per_second
        )
        self.last = now
        if self.active >= self.concurrency_limit or self.tokens < 1:
            return await reject(429, "try_later")
        self.tokens -= 1
        self.active += 1
        try:
            body = bytearray()
            try:
                with fail_after(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        if message["type"] != "http.request":
                            return await reject(400, "invalid_request")
                        body.extend(message.get("body", b""))
                        if len(body) > self.body_limit(path, method):
                            return await reject(413, "request_too_large")
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "request_timeout")
            delivered = False

            async def buffered():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, buffered, send)
        finally:
            self.active -= 1


def create_app():
    """Require a separately reviewed v5 read-only pilot, never the v6 listener."""
    from devgraph.signed_read_runtime import create_app as create_read_app

    path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not path:
        raise RuntimeError("public pilot requires explicit read configuration")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    if config.schema_ != "devgraph.signed-read-runtime.v5":
        raise ValueError("public pilot requires read-only runtime v5")
    origin = public_origin(os.environ["DEVGRAPH_PUBLIC_ORIGIN"])
    name = os.environ["DEVGRAPH_PUBLIC_NAME"]
    expiry = int(os.environ["DEVGRAPH_PUBLIC_EXPIRES_AT"])
    if not 1 <= len(name) <= 80 or not time.time() < expiry <= time.time() + 10800:
        raise ValueError("bounded public pilot window and name required")
    root = private_json(config.enrollment_policy_file)
    if root["receiver"] != origin:
        raise ValueError("public origin must match the signed receiver")
    approved_digest = os.environ.get("DEVGRAPH_PUBLIC_OPERATOR_POLICY_SHA256")
    approved_operator_policy = None
    if approved_digest is not None:
        if re.fullmatch(r"[0-9a-f]{64}", approved_digest) is None:
            raise ValueError("explicit reviewed operator policy digest required")
        from devgraph.auth.access_operator import OperatorPolicy

        policy = OperatorPolicy.model_validate(private_json(config.access_operator_policy_file))
        if (
            policy.ceiling.write_grants
            or policy.ceiling.not_after > expiry
            or any(not grant.work_ids for grant in policy.ceiling.read_grants)
        ):
            raise ValueError(
                "public operator pilot requires bounded explicit-object read-only policy"
            )

        def approved_operator_policy():
            try:
                raw = private_json(config.access_operator_policy_file)
                return hashlib.sha256(
                    canonical_json(raw)
                ).hexdigest() == approved_digest and raw == policy.model_dump(
                    mode="json", by_alias=True, exclude_defaults=True
                )
            except (OSError, ValueError):
                return False

        if not approved_operator_policy():
            raise ValueError("operator policy differs from separately reviewed bytes")
    app = create_read_app()
    app.state.access_operator_enabled = approved_operator_policy is not None
    authority = private_json(config.authority_policy_file)
    profile = dict(
        name=name,
        receiver=origin,
        issuer=root["issuer"],
        authority={
            key: authority[key]
            for key in ("issuer", "key_id", "public_key", "receiver", "audience")
        },
    )

    @app.get("/.well-known/devgraph-connection.json", include_in_schema=False)
    def connection():
        return profile

    @app.get("/live", include_in_schema=False)
    def live():
        return dict(live=True, mode="read-only-public-pilot")

    return PublicReadBoundary(
        app,
        origin=origin,
        expires_at=expiry,
        approved_operator_policy=approved_operator_policy,
        canonical_paths=config.canonical_access_points,
    )
