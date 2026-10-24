"""Wallet-authenticated LAN member access; administration remains local secS RPC."""

import os
from pathlib import Path

from devgraph.lan_access_runtime import LanAccessBoundary
from devgraph.lan_read_runtime import lan_origin
from devgraph.local_services import instance_id
from devgraph.public_read_runtime import GET_PATHS, OPERATOR_POST_PATHS
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


class OwnerLANBoundary(LanAccessBoundary):
    # Do not publish the browser operator UI or unsigned operator challenges.
    # Native Transfer uses the same operator directly over verified local RPC.
    get_paths = GET_PATHS | {
        "/devgraph/access-requests/v2/catalog",
        "/.well-known/devgraph-wallet-service.json",
    }
    post_paths = LanAccessBoundary.post_paths - OPERATOR_POST_PATHS
    asset_patterns = ()


def create_app():
    from devgraph.owner_endpoint import endpoint_operator_policy
    from devgraph.signed_read_runtime import create_app as create_signed_app

    config = ReadRuntimeConfiguration.model_validate(
        private_json(Path(os.environ["DEVGRAPH_SIGNED_READ_CONFIG"]))
    )
    endpoint_operator_policy(config, data_root=os.environ["DEVGRAPH_DATA_ROOT"])
    profile = private_json(config.owner_endpoint_profile_file)
    origin = lan_origin(profile["receiver"])
    if os.environ["DEVGRAPH_LAN_ORIGIN"] != origin:
        raise ValueError("selected LAN endpoint changed")
    review = private_json(config.owner_install_review_file, maximum_bytes=262144)
    identity = instance_id(review["resource_id"], "wallet-api", origin)
    app = create_signed_app()

    @app.get("/.well-known/devgraph-connection.json", include_in_schema=False)
    def connection():
        return {k: profile[k] for k in ("name", "receiver", "issuer", "authority")}

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
            mode="owner-lan-wallet-access",
            resource_id=review["resource_id"],
            instance_id=identity,
            access_requires_current_grant=True,
        )

    return OwnerLANBoundary(app, origin=origin)
