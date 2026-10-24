"""Unclaimed loopback Wallet service: discovery works, authority stays closed.

There is no HTTP owner-claim route. The protected owner-local setup adapter must
register ownership and perform a separate reviewed activation. This phase never
opens the graph or loads its database password, native signer or bearer.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import JSONResponse

from devgraph.lan_access_runtime import LanAccessBoundary
from devgraph.local_access_runtime import LocalAccessBoundary, local_origin
from devgraph.local_services import instance_id
from devgraph.ops.owner_install import object_file


class UnclaimedBoundary(LocalAccessBoundary):
    get_paths = frozenset(
        (
            "/live",
            "/.well-known/devgraph-connection.json",
            "/.well-known/devgraph-wallet-service.json",
        )
    )
    post_paths = LanAccessBoundary.post_paths


def create_unclaimed_app(deployment):
    origin = local_origin(deployment["endpoint"])
    expected = instance_id(deployment["resource_id"], "wallet-api", origin)
    if deployment["instance_id"] != expected or deployment["state"] != "unclaimed":
        raise ValueError("explicit unclaimed service required")
    profile = deployment["connection"]
    if profile["receiver"] != origin or profile["authority"]["receiver"] != origin:
        raise ValueError("selected service changed")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/live")
    def live():
        return dict(
            live=True,
            mode="owner-local-wallet-unclaimed",
            resource_id=deployment["resource_id"],
            instance_id=expected,
            owner_registered=False,
            administration_enabled=False,
        )

    @app.get("/.well-known/devgraph-connection.json")
    def connection():
        return {key: profile[key] for key in ("name", "receiver", "issuer", "authority")}

    @app.get("/.well-known/devgraph-wallet-service.json")
    def capabilities():
        return dict(
            schema="devgraph.wallet-service.v1",
            resource_id=deployment["resource_id"],
            instance_id=expected,
            endpoint=origin,
            state="owner_required",
            owner_registration="protected-local-wallet-proof",
            administration_enabled=False,
        )

    # Do not accept member, operator, arbitrary Work or remote owner requests
    # before first-owner activation. No auth/graph router is mounted here.
    @app.post("/{path:path}")
    def closed(path: str):
        return JSONResponse({"error": "owner_setup_required"}, status_code=503)

    return UnclaimedBoundary(app, origin=origin)


def create_app():
    path = os.environ.get("DEVGRAPH_WALLET_SERVICE_DEPLOYMENT")
    if not path:
        raise RuntimeError("explicit private deployment required")
    from devgraph.ops.wallet_service import validate_deployment

    deployment = validate_deployment(object_file(Path(path)), root=Path(path).parent)
    return create_unclaimed_app(deployment)
