"""Owner-published HTTPS member entry; owner administration stays on local RPC.

TLS may terminate on an explicitly configured dedicated loopback proxy socket.
Forwarded headers never select a receiver, protocol, client identity or authority.
The owning OS account is already trusted with the protected runtime and issuer.
"""

import os
from pathlib import Path

from starlette.responses import JSONResponse

from devgraph.endpoint_read_runtime import EndpointReadBoundary
from devgraph.local_services import instance_id
from devgraph.owner_endpoint import endpoint_operator_policy
from devgraph.public_read_runtime import PublicReadBoundary, public_origin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


class _PageAssets(EndpointReadBoundary):
    get_paths = frozenset(("/", "/monitor", "/monitor/kanban", "/monitor/kanban/"))
    post_paths = frozenset()
    concurrency_limit = 32
    burst_capacity = 120.0
    refill_per_second = 8


class OwnerHTTPSBoundary(EndpointReadBoundary):
    get_paths = EndpointReadBoundary.get_paths | {"/.well-known/devgraph-wallet-service.json"}

    def __init__(self, app, *, origin, canonical_paths=False, native_session_origins=()):
        super().__init__(
            app,
            origin=origin,
            canonical_paths=canonical_paths,
            native_session_origins=native_session_origins,
        )
        self.assets = _PageAssets(app, origin=origin)

    async def __call__(self, scope, receive, send):
        # A normal browser loads more than eight scripts/styles concurrently.
        # Those fixed public assets must not consume signed-command capacity.
        if (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and (
                scope["path"] in self.assets.get_paths
                or any(p.fullmatch(scope["path"]) for p in self.assets.asset_patterns)
            )
        ):
            return await self.assets(scope, receive, send)
        return await PublicReadBoundary.__call__(self, scope, receive, send)


class DedicatedTLSProxy:
    """Fixed local TLS terminator, with exact socket and downstream Host checks.

    This is transport admission only. Every grant, holder signature, expiration,
    replay, receiver and owner check still runs in the signed member composition.
    Uvicorn must bind 127.0.0.1 and disable its proxy-header middleware.
    """

    def __init__(self, app, *, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        if (
            scope["type"] != "http"
            or scope.get("scheme") != "http"
            or tuple(scope.get("server") or ()) != ("127.0.0.1", self.port)
            or not client
            or client[0] != "127.0.0.1"
        ):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse({"error": "invalid_tls_proxy"}, status_code=421)(
                scope, receive, send
            )
        scope = dict(scope, scheme="https")
        scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() != b"forwarded" and not key.lower().startswith(b"x-forwarded-")
        ]
        return await self.app(scope, receive, send)


def create_app():
    from devgraph.signed_read_runtime import create_app as create_signed_app

    config = ReadRuntimeConfiguration.model_validate(
        private_json(Path(os.environ["DEVGRAPH_SIGNED_READ_CONFIG"]))
    )
    endpoint_operator_policy(config, data_root=os.environ["DEVGRAPH_DATA_ROOT"])
    profile = private_json(config.owner_endpoint_profile_file)
    origin = public_origin(os.environ["DEVGRAPH_ENDPOINT_ORIGIN"])
    if profile["receiver"] != origin:
        raise ValueError("selected HTTPS endpoint changed")
    review = private_json(config.owner_install_review_file, maximum_bytes=262144)
    identity = instance_id(review["resource_id"], "wallet-api", origin)
    app = create_signed_app()
    if not app.state.access_requests_enabled:
        raise ValueError("member credential requests required")
    app.state.access_operator_enabled = False

    @app.get("/.well-known/devgraph-connection.json", include_in_schema=False)
    def connection():
        return {key: profile[key] for key in ("name", "receiver", "issuer", "authority")}

    @app.get("/.well-known/devgraph-wallet-service.json", include_in_schema=False)
    def capabilities():
        return dict(
            schema="devgraph.wallet-service.v1",
            resource_id=review["resource_id"],
            instance_id=identity,
            endpoint=origin,
            state="owner_registered",
            administration_enabled=False,
            member_access_enabled=True,
        )

    @app.get("/live", include_in_schema=False)
    def live():
        return dict(
            live=True,
            mode="owner-https-wallet-member-access",
            resource_id=review["resource_id"],
            instance_id=identity,
            credential_requests_enabled=True,
            access_requires_current_grant=True,
        )

    from devgraph.api.holder_sessions import configured_native_origins

    boundary = OwnerHTTPSBoundary(
        app,
        origin=origin,
        canonical_paths=config.canonical_access_points,
        native_session_origins=configured_native_origins(
            getattr(getattr(app.state, "session_access_components", None), "enrollment", None)
        ),
    )
    return (
        DedicatedTLSProxy(boundary, port=config.endpoint_tls_proxy_port)
        if config.endpoint_tls_proxy_port is not None
        else boundary
    )
