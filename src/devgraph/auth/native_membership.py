"""Verification of the frozen public Zenith v3 and scoped v4 membership bytes.

Trust is operator-provided; incoming files/proofs cannot install roots. The
revoked-membership predicate is evaluated on enrollment and every read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ID = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
HEX = re.compile(r"^[0-9a-f]{64}$")


def _exact(value: dict, fields: tuple[str, ...]) -> None:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("invalid_membership")


def _hex(value: str) -> bytes:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("invalid_membership")
    return bytes.fromhex(value)


def _id(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid_membership")
    return value


def _json(domain: bytes, value: dict) -> bytes:
    # v4 is insertion-ordered JSON, deliberately not JCS or sorted-key JSON.
    return (
        domain
        + json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )


def _scopes(scopes: list) -> None:
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 128:
        raise ValueError("invalid_trust")
    seen = set()
    for scope in scopes:
        _exact(scope, ("scopeId", "genesis", "issuerIds"))
        _hex(scope["scopeId"])
        if scope["scopeId"] in seen:
            raise ValueError("duplicate_scope")
        seen.add(scope["scopeId"])
        genesis = scope["genesis"]
        keys = ("schema", "version", "scopeType", "alias", "nonce")
        _exact(genesis, keys)
        if (
            genesis["schema"] != "castalia.community-genesis.v1"
            or type(genesis["version"]) is not int
            or genesis["version"] != 1
            or genesis["scopeType"] != "community"
        ):
            raise ValueError("invalid_genesis")
        _id(genesis["alias"])
        _hex(genesis["nonce"])
        if (
            hashlib.sha256(
                _json(b"castalia/community-id/v1\0", {k: genesis[k] for k in keys})
            ).hexdigest()
            != scope["scopeId"]
        ):
            raise ValueError("invalid_genesis")
        issuers = scope["issuerIds"]
        if not isinstance(issuers, list) or not 1 <= len(issuers) <= 128:
            raise ValueError("invalid_trust")
        if len({_id(issuer) for issuer in issuers}) != len(issuers):
            raise ValueError("duplicate_issuer")


class ZenithMembershipValidator:
    def __init__(self, trust: Callable[[], dict], revoked: Callable[[str], bool]):
        self.trust = trust
        self.revoked = revoked

    def __call__(self, proof: dict, owner: str) -> bool:
        try:
            self._validate(proof, owner)
            return True
        except (ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
            return False

    def _validate(self, proof: dict, owner: str) -> None:
        common = (
            "schema",
            "version",
            "membershipId",
            "ownerPublicKey",
            "status",
            "issuerId",
            "issuerKeyId",
            "signatureSuite",
            "issuerSignature",
        )
        scoped = proof.get("version") == 4
        _exact(proof, common + (("scopeType", "scopeId") if scoped else ()))
        if (
            proof["schema"]
            != (
                "castalia.scoped-membership-credential.v4"
                if scoped
                else "castalia.zenith-membership-credential.v3"
            )
            or type(proof["version"]) is not int
            or proof["version"] != (4 if scoped else 3)
            or proof["ownerPublicKey"] != owner
            or proof["status"] != "active"
            or proof["signatureSuite"] != "Ed25519"
        ):
            raise ValueError("invalid_membership")
        owner_bytes = _hex(owner)
        _id(proof["issuerId"])
        _id(proof["issuerKeyId"])
        policy = self.trust()["v4" if scoped else "v3"]
        _exact(policy, ("schema", "version", "roots") + (("scopes",) if scoped else ()))
        if (
            policy["schema"]
            != (
                "castalia.scoped-membership-trust-policy.v1"
                if scoped
                else "castalia.zenith-membership-trust-policy.v1"
            )
            or type(policy["version"]) is not int
            or policy["version"] != 1
        ):
            raise ValueError("invalid_trust")
        roots = policy["roots"]
        if not isinstance(roots, list) or not 1 <= len(roots) <= 128:
            raise ValueError("invalid_trust")
        seen = set()
        for root in roots:
            _exact(root, ("issuerId", "keyId", "signatureSuite", "publicKey"))
            identity = (_id(root["issuerId"]), _id(root["keyId"]))
            if identity in seen or root["signatureSuite"] != "Ed25519":
                raise ValueError("invalid_trust")
            seen.add(identity)
            _hex(root["publicKey"])
        root = next(
            (
                r
                for r in roots
                if r["issuerId"] == proof["issuerId"] and r["keyId"] == proof["issuerKeyId"]
            ),
            None,
        )
        if not root:
            raise ValueError("untrusted_issuer")
        prefix = (
            b"castalia/scoped-membership-id/v4\0"
            if scoped
            else b"castalia/zenith-membership-id/v3\0"
        )
        identity_bytes = prefix + owner_bytes
        if scoped:
            if proof["scopeType"] != "community":
                raise ValueError("invalid_membership")
            identity_bytes += _hex(proof["scopeId"])
            scopes = policy["scopes"]
            _scopes(scopes)
            scope = next((s for s in scopes if s["scopeId"] == proof["scopeId"]), None)
            if not scope or proof["issuerId"] not in scope["issuerIds"]:
                raise ValueError("untrusted_scope")
        if hashlib.sha256(identity_bytes).hexdigest() != proof["membershipId"] or self.revoked(
            proof["membershipId"]
        ):
            raise ValueError("invalid_membership")
        if scoped:
            keys = (
                "schema",
                "version",
                "scopeType",
                "scopeId",
                "membershipId",
                "ownerPublicKey",
                "status",
                "issuerId",
                "issuerKeyId",
                "signatureSuite",
            )
            transcript = _json(
                b"castalia/scoped-membership-credential/v4\0", {k: proof[k] for k in keys}
            )
        else:
            transcript = (
                b"castalia/zenith-membership-credential/v3\0"
                + (3).to_bytes(8, "little")
                + _hex(proof["membershipId"])
                + owner_bytes
            )
            for value in ("active", proof["issuerId"], proof["issuerKeyId"], "ed25519"):
                transcript += len(value.encode()).to_bytes(4, "little") + value.encode()
        raw = proof["issuerSignature"]
        if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", raw):
            raise ValueError("invalid_signature")
        signature = base64.urlsafe_b64decode(raw + "==")
        if base64.urlsafe_b64encode(signature).rstrip(b"=").decode() != raw:
            raise ValueError("invalid_signature")
        Ed25519PublicKey.from_public_bytes(_hex(root["publicKey"])).verify(signature, transcript)
