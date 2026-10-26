"""Explicit HTTPS member entry point, independent of the network transport.

Existing signed request, operator decision and Wallet credential contracts apply.
This factory never creates authority, changes a receiver, or publishes a route.
Operator administration and Work mutations stay on their separately enabled host.
"""

import argparse
import json
import os
from pathlib import Path

from devgraph.public_read_runtime import GET_PATHS, PublicReadBoundary, public_origin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


class EndpointReadBoundary(PublicReadBoundary):
    get_paths = GET_PATHS | {"/devgraph/access-requests/v2/catalog"}

    def __init__(self, app, *, origin, canonical_paths=False):
        super().__init__(
            app, origin=public_origin(origin), expires_at=0, canonical_paths=canonical_paths
        )

    def expired(self):
        # Listener lifetime is independent of grants and credential expirations.
        return False


def review_endpoint(config_path, origin):
    """Read public binding metadata without opening stores or loading key bytes."""
    origin = public_origin(origin)
    config = private_json(Path(config_path))
    root = private_json(Path(config["enrollment_policy_file"]))
    authority = private_json(Path(config["authority_policy_file"]))
    blockers = []
    if config["schema"] not in {
        "devgraph.signed-read-runtime.v5",
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v11",
    }:
        blockers.append("A request-enabled signed-read configuration is required.")
    if root["receiver"] != origin or authority["receiver"] != origin:
        blockers.append("The selected HTTPS origin needs its own reviewed receiver binding.")
    if root["issuer"] != authority["issuer"]:
        blockers.append("Enrollment and credential issuer bindings disagree.")
    for field in ("access_inbox_database_file", "access_operator_policy_file"):
        if not config.get(field):
            blockers.append("The member request inbox and operator policy must be configured.")
            break
    if config.get("access_operator_policy_file"):
        operator = private_json(Path(config["access_operator_policy_file"]))
        ceiling = operator["ceiling"]
        if ceiling["receiver"] != origin or ceiling["issuer"] != root["issuer"]:
            blockers.append("The operator policy must authorize this exact receiver and issuer.")
    return dict(
        schema="devgraph.endpoint-wallet-review.v1",
        origin=origin,
        current_receiver=root["receiver"],
        issuer=root["issuer"],
        runtime_schema=config["schema"],
        blockers=blockers,
        ready_for_startup_validation=not blockers,
        authority_changed=False,
        network_changed=False,
        grants_created=0,
    )


def create_app():
    from devgraph.signed_read_runtime import create_app as create_signed_app

    path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not path:
        raise RuntimeError("endpoint requires explicit signed-read configuration")
    origin = public_origin(os.environ["DEVGRAPH_ENDPOINT_ORIGIN"])
    review = review_endpoint(path, origin)
    if review["blockers"]:
        raise ValueError("endpoint receiver and request authority must match before startup")
    # The full composition validates the version and all existing authority,
    # custody, owner-registration and native-admission preconditions unchanged.
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    authority = private_json(config.authority_policy_file)
    name = os.environ.get("DEVGRAPH_ENDPOINT_NAME", "Devgraph")
    if not 1 <= len(name) <= 80 or not name.strip():
        raise ValueError("bounded endpoint name required")
    app = create_signed_app()
    if not getattr(app.state, "access_requests_enabled", False):
        raise ValueError("signed member request routes are required")
    # The external entry point exposes member requests, never operator UI/actions.
    app.state.access_operator_enabled = False
    profile = dict(
        name=name,
        receiver=origin,
        issuer=review["issuer"],
        authority={
            key: authority[key]
            for key in ("issuer", "key_id", "public_key", "receiver", "audience")
        },
    )

    @app.get("/.well-known/devgraph-connection.json", include_in_schema=False)
    def connection():
        # Route selection belongs to the client. No Tailnet membership or
        # public/private transport is inferred from the server hostname.
        return profile

    @app.get("/live", include_in_schema=False)
    def live():
        return dict(
            live=True,
            mode="https-wallet-member-access",
            access_requires_current_grant=True,
            credential_requests_enabled=True,
        )

    return EndpointReadBoundary(app, origin=origin, canonical_paths=config.canonical_access_points)


def main():
    parser = argparse.ArgumentParser(description="Review an HTTPS Wallet endpoint without changes")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    try:
        result = review_endpoint(args.config, args.origin)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "invalid_endpoint_configuration"}))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["ready_for_startup_validation"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
