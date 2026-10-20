"""Explicit private-LAN read listener, independent of credential validity.

This never provisions authority or exposes Work writes/operator administration.
The public pilot keeps its bounded lifetime and separate factory unchanged.
"""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from devgraph.public_read_runtime import PublicReadBoundary, public_origin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


def lan_origin(value: str) -> str:
    origin = public_origin(value)
    address = ipaddress.ip_address(urlsplit(origin).hostname)
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in networks):
        raise ValueError("explicit RFC1918 IPv4 LAN origin required")
    return origin


class LanReadBoundary(PublicReadBoundary):
    def __init__(self, app, *, origin):
        super().__init__(app, origin=lan_origin(origin), expires_at=0)

    def expired(self):
        # The listener has no test timer. Downstream signed grants, credentials,
        # revocation and policy validity remain enforced on every request.
        return False


def create_app():
    from devgraph.signed_read_runtime import create_app as create_read_app

    config_path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not config_path:
        raise RuntimeError("LAN listener requires explicit read configuration")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(config_path)))
    if config.schema_ != "devgraph.signed-read-runtime.v5":
        raise ValueError("LAN read listener requires read-only runtime v5")
    origin = lan_origin(os.environ["DEVGRAPH_LAN_ORIGIN"])
    root = private_json(config.enrollment_policy_file)
    authority = private_json(config.authority_policy_file)
    if root["receiver"] != origin or authority["receiver"] != origin:
        raise ValueError("LAN origin must match the signed receiver")
    name = os.environ.get("DEVGRAPH_LAN_NAME", "Devgraph")
    if not 1 <= len(name) <= 80:
        raise ValueError("bounded LAN name required")
    app = create_read_app()
    app.state.access_operator_enabled = False
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
        return dict(live=True, mode="lan-read", access_requires_current_grant=True)

    return LanReadBoundary(app, origin=origin)
