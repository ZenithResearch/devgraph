"""Loopback HTTPS Wallet administration on an explicitly reviewed real graph.

The private API and its existing authentication are not replaced or proxied.
No external listener, browser administration page or owner bootstrap route.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from devgraph.lan_access_runtime import LanAccessBoundary
from devgraph.local_services import instance_id
from devgraph.public_read_runtime import PublicReadBoundary, public_origin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


def local_origin(value):
    value = public_origin(value)
    parsed = urlsplit(value)
    if (
        value != f"https://127.0.0.1:{parsed.port}"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or not 1024 <= parsed.port <= 65535
        or parsed.port in (8080, 7687)
    ):
        raise ValueError("explicit loopback Wallet origin required")
    return value


class LocalAccessBoundary(LanAccessBoundary):
    get_paths = frozenset(
        (
            "/.well-known/devgraph-connection.json",
            "/live",
            "/devgraph/access-requests/v2/catalog",
            "/monitor/wallet-access/v1",
        )
    )
    asset_patterns = ()

    def __init__(self, app, *, origin):
        PublicReadBoundary.__init__(self, app, origin=local_origin(origin), expires_at=0)


def create_app():
    from devgraph.signed_read_runtime import create_app as signed_app

    path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not path:
        raise RuntimeError("explicit owner configuration required")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    if (
        config.schema_
        not in ("devgraph.signed-read-runtime.v9", "devgraph.signed-read-runtime.v11")
        or config.owner_bootstrap_database_file is None
    ):
        raise ValueError("owner-registered runtime required")
    origin = local_origin(os.environ["DEVGRAPH_LAN_ORIGIN"])
    root = private_json(config.enrollment_policy_file)
    authority = private_json(config.authority_policy_file)
    if root["receiver"] != origin or authority["receiver"] != origin:
        raise ValueError("selected Wallet receiver changed")
    name = os.environ["DEVGRAPH_LAN_NAME"]
    if not 1 <= len(name) <= 80:
        raise ValueError("bounded resource name required")
    review = private_json(config.owner_install_review_file, maximum_bytes=262144)
    service_id = instance_id(review["resource_id"], "wallet-api", origin)
    app = signed_app()
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
        return dict(
            live=True,
            mode="owner-local-wallet-administration",
            access_requires_current_grant=True,
            resource_id=review["resource_id"],
            instance_id=service_id,
        )

    return LocalAccessBoundary(app, origin=origin)
