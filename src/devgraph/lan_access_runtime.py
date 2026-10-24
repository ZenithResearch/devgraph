"""Explicit LAN-only standing-access listener; legacy read/public modes stay closed.

This factory creates no identity, grant, trust, fixture data or native admission.
Owner installation must separately pin the real native policy and signing key.
"""

import os
from pathlib import Path

from devgraph.lan_read_runtime import lan_origin
from devgraph.public_read_runtime import (
    ASSET,
    GET_PATHS,
    OPERATOR_ASSET,
    OPERATOR_GET_PATHS,
    OPERATOR_POST_PATHS,
    POST_PATHS,
    PublicReadBoundary,
)
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json

CREDENTIAL_PATHS = frozenset(
    "/credential-work/v2/" + suffix
    for suffix in ("capabilities", "provider-profile", "prepare", "execute", "status")
)
NAMED_PATHS = frozenset(
    (
        "/work-operations/v2",
        "/work-operations/v2/status",
        "/todo-operations/v2",
        "/todo-operations/v2/status",
    )
)


class LanAccessBoundary(PublicReadBoundary):
    get_paths = GET_PATHS | OPERATOR_GET_PATHS | {"/devgraph/access-requests/v2/catalog"}
    post_paths = POST_PATHS | OPERATOR_POST_PATHS | CREDENTIAL_PATHS | NAMED_PATHS
    asset_patterns = (ASSET, OPERATOR_ASSET)

    def __init__(self, app, *, origin):
        super().__init__(app, origin=lan_origin(origin), expires_at=0)

    def expired(self):
        return False

    def body_limit(self, path, method):
        if method != "POST":
            return 0
        if path in CREDENTIAL_PATHS:
            return 262144
        if path in NAMED_PATHS:
            return 131072
        return 65536


def create_app():
    from devgraph.signed_read_runtime import create_app as create_signed_app

    path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not path:
        raise RuntimeError("LAN access requires explicit standing configuration")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    if config.schema_ not in (
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
    ):
        raise ValueError("LAN access requires explicit runtime v8")
    origin = lan_origin(os.environ["DEVGRAPH_LAN_ORIGIN"])
    root = private_json(config.enrollment_policy_file)
    authority = private_json(config.authority_policy_file)
    if root["receiver"] != origin or authority["receiver"] != origin:
        raise ValueError("LAN origin must match the signed receiver")
    name = os.environ.get("DEVGRAPH_LAN_NAME", "Devgraph")
    if not 1 <= len(name) <= 80:
        raise ValueError("bounded LAN name required")
    app = create_signed_app()
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
        return dict(live=True, mode="lan-standing-access", access_requires_current_grant=True)

    return LanAccessBoundary(app, origin=origin)
