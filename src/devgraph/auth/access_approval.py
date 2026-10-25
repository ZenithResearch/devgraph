"""Application-neutral Wallet consent envelope; no credential or grant issuance."""

import base64
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator

from devgraph.auth.access_requests import Closed
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError

DOMAIN = b"castalia/access-request-approval/v1\0"
MAX_OPERATOR_CHALLENGE_BYTES = 131072
MAX_MEMBER_CHALLENGE_BYTES = 16384
OPERATIONS = ("access.request", "access.status", "access.inbox", "access.review", "access.decide")


def portable(value, depth=0):
    if depth > 16:
        return False
    if type(value) in (str, bool, type(None)):
        return True
    if type(value) is int:
        return abs(value) <= 9007199254740991
    if isinstance(value, list):
        return all(portable(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and k.isascii() and portable(v, depth + 1) for k, v in value.items()
        )
    return False


class WalletAccessChallenge(Closed):
    schema_: Literal["castalia.access-request-challenge.v1"] = Field(alias="schema")
    challenge_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    issuer: str
    receiver: str
    subject_public_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    operation: Literal[
        "access.request", "access.status", "access.inbox", "access.review", "access.decide"
    ]
    issued_at: int = Field(strict=True, ge=0, le=9007199254740991)
    expires_at: int = Field(strict=True, ge=1, le=9007199254740991)
    details: dict

    @model_validator(mode="after")
    def valid(self):
        value = self.model_dump(mode="json", by_alias=True)
        maximum = (
            MAX_MEMBER_CHALLENGE_BYTES
            if self.operation in ("access.request", "access.status")
            else MAX_OPERATOR_CHALLENGE_BYTES
        )
        if not portable(value) or len(canonical_json(value)) > maximum:
            raise ValueError("invalid access request")
        return self


def verify_wallet_approval(challenge, signature, *, issuer, receiver, current):
    try:
        parsed = WalletAccessChallenge.model_validate(challenge)
        if (
            parsed.issuer != issuer
            or parsed.receiver != receiver
            or not parsed.issued_at <= current < parsed.expires_at
            or parsed.expires_at - parsed.issued_at != 120
            or not isinstance(signature, str)
            or len(signature) != 86
        ):
            raise ValueError()
        raw = base64.urlsafe_b64decode(signature + "==")
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != signature:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(parsed.subject_public_key)).verify(
            raw, DOMAIN + canonical_json(challenge)
        )
        return parsed
    except (ValueError, TypeError, InvalidSignature):
        raise UnauthenticatedError("invalid_access_approval") from None
