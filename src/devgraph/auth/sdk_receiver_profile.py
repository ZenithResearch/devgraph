"""Public, non-authorizing configuration pin for the fixed local SDK receiver.

The origin is installation policy, never an HTTP Host or Origin header. Version
one supports only the selected local preview topology. This hash is neither an
authenticated discovery mechanism nor a cryptographic execution receipt.
"""

from __future__ import annotations

import hashlib

from devgraph.auth.secs_issue_create import _AUTHORITY_IDENTIFIER, _canonical_json

RECEIVER_PROFILE_SCHEMA = "devgraph.sdk-receiver-profile.v1"
RECEIVER_PROFILE_DOMAIN = b"devgraph.sdk-receiver-profile.v1\x00"
RECEIVER_PROFILE_HEADER = "X-Devgraph-Receiver-Profile"
SDK_RECEIVER_ORIGIN = "http://127.0.0.1:8080"
SDK_RECEIVER_AUDIENCE = "devgraph://receiver-local"


def receiver_profile_canonical(
    *,
    stable_issuer: str,
    audience: str = SDK_RECEIVER_AUDIENCE,
    origin: str = SDK_RECEIVER_ORIGIN,
) -> bytes:
    """Encode the four-field profile using the existing sorted JSON contract."""
    if (
        origin != SDK_RECEIVER_ORIGIN
        or audience != SDK_RECEIVER_AUDIENCE
        or not isinstance(stable_issuer, str)
        or _AUTHORITY_IDENTIFIER.fullmatch(stable_issuer) is None
    ):
        raise ValueError("invalid SDK receiver profile")
    return _canonical_json({
        "schema": RECEIVER_PROFILE_SCHEMA,
        "origin": origin,
        "audience": audience,
        "stable_issuer": stable_issuer,
    })


def receiver_profile_digest(
    *,
    stable_issuer: str,
    audience: str = SDK_RECEIVER_AUDIENCE,
    origin: str = SDK_RECEIVER_ORIGIN,
) -> str:
    return hashlib.sha256(
        RECEIVER_PROFILE_DOMAIN + receiver_profile_canonical(
            stable_issuer=stable_issuer, audience=audience, origin=origin,
        )
    ).hexdigest()
