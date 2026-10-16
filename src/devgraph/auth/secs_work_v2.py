"""Receiver for the separate generic-presentation Work authority v2 contract."""

from __future__ import annotations

import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devgraph.auth.secs_issue_create import (
    _ACTOR_ID,
    _AUTHORITY_IDENTIFIER,
    _ED25519_SCALAR_ORDER,
    _LOWER_HEX_64,
    _SAFE_LABEL_128,
    _SAFE_LABEL_256,
    _SECS_CONTEXT_ID,
    SecSIssueCreateDenied,
    SecSIssueCreateVerifierConfig,
    _canonical_json,
    _decode_base64url,
    _digest_idempotency_key,
    _is_strict_ed25519_point_encoding,
    _require_nonnegative_safe_integer,
    _strict_json_object,
)
from devgraph.auth.secs_work import SecSWorkDenied, _Principal, _VerifiedWork
from devgraph.credential_requests import credential_request_binding
from devgraph.named_requests import parse_named_request
from devgraph.work_requests import InvalidWorkRequest

SCHEMA = "secs-devgraph-work-authority.v2"
SIGNATURE_DOMAIN = b"secs-devgraph-work-authority.v2/signature\x00"
PROJECTION_DOMAIN = b"secs-devgraph-work-authority.v2/projection\x00"
_FIELDS = frozenset(
    {
        "actor_id",
        "actor_signature_suite",
        "audience",
        "expires_at",
        "idempotency_key_digest_sha256",
        "issued_at",
        "nonce",
        "operation",
        "receiver_policy_digest_sha256",
        "receiver_policy_id",
        "receiver_policy_version",
        "replay_scope",
        "request_digest_sha256",
        "resources",
        "schema",
        "schema_version",
        "secs_context_id",
        "secs_verifier_key_id",
        "secs_verifier_signature",
        "secs_verifier_signature_suite",
        "session_id",
        "credential_presentation_digest_sha256",
        "credential_digest_sha256",
        "disclosure_digest_sha256",
        "credential_request_digest_sha256",
    }
)


class SecSWorkV2Verifier:
    def __init__(self, config: SecSIssueCreateVerifierConfig):
        # Reuse trusted-key and policy-pin registries, never the Issue v1 parser.
        if not isinstance(config, SecSIssueCreateVerifierConfig):
            raise TypeError("invalid named Work verifier configuration")
        self.config = config

    def verify(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        try:
            request = parse_named_request(request_json)
            projection = _strict_json_object(
                projection_json, maximum_bytes=16_384, reason="invalid_work_projection"
            )
            if set(projection) != _FIELDS:
                raise SecSWorkDenied()
            exact = {
                "schema": SCHEMA,
                "operation": request.authority_operation,
                "actor_signature_suite": "Ed25519",
                "secs_verifier_signature_suite": "Ed25519",
                "replay_scope": "credential:operation:nonce",
                "audience": self.config.audience,
            }
            if any(projection[key] != value for key, value in exact.items()):
                raise SecSWorkDenied()
            if type(projection["schema_version"]) is not int or projection["schema_version"] != 2:
                raise SecSWorkDenied()
            try:
                now = self.config.clock()
            except Exception:
                raise SecSWorkDenied("clock_unavailable") from None
            _require_nonnegative_safe_integer(now, reason="clock_unavailable")
            for field in ("issued_at", "expires_at", "receiver_policy_version"):
                _require_nonnegative_safe_integer(
                    projection[field], reason="invalid_work_projection"
                )
            if not (projection["issued_at"] <= now < projection["expires_at"]):
                raise SecSWorkDenied("named_work_authority_not_current")
            if not 0 < projection["expires_at"] - projection["issued_at"] <= 60:
                raise SecSWorkDenied()
            for field, pattern in (
                ("actor_id", _ACTOR_ID),
                ("secs_context_id", _SECS_CONTEXT_ID),
                ("secs_verifier_key_id", _SAFE_LABEL_256),
                ("receiver_policy_id", _SAFE_LABEL_128),
                ("audience", _AUTHORITY_IDENTIFIER),
                *[
                    (field, _LOWER_HEX_64)
                    for field in (
                        "request_digest_sha256",
                        "idempotency_key_digest_sha256",
                        "credential_presentation_digest_sha256",
                        "credential_digest_sha256",
                        "disclosure_digest_sha256",
                        "credential_request_digest_sha256",
                        "receiver_policy_digest_sha256",
                    )
                ],
            ):
                if (
                    not isinstance(projection[field], str)
                    or pattern.fullmatch(projection[field]) is None
                ):
                    raise SecSWorkDenied()
            for field in ("session_id", "nonce"):
                value = projection[field]
                if (
                    not isinstance(value, str)
                    or len(value) != 32
                    or any(c not in "0123456789abcdef" for c in value)
                ):
                    raise SecSWorkDenied("invalid_work_projection")
            signature = _decode_base64url(
                projection["secs_verifier_signature"],
                encoded_length=86,
                decoded_length=64,
                reason="invalid_work_signature",
            )
            if not _is_strict_ed25519_point_encoding(signature[:32]) or (
                int.from_bytes(signature[32:], "little") >= _ED25519_SCALAR_ORDER
            ):
                raise SecSWorkDenied()
            key = self.config.key_registry.require_production_key(
                projection["secs_verifier_key_id"], now=now
            )
            unsigned = dict(projection)
            unsigned.pop("secs_verifier_signature")
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                signature, SIGNATURE_DOMAIN + _canonical_json(unsigned)
            )
            self.config.policy_registry.require_binding(
                projection["receiver_policy_id"],
                projection["receiver_policy_version"],
                projection["receiver_policy_digest_sha256"],
            )
            idempotency_digest = _digest_idempotency_key(idempotency_key)
            if (
                not hmac.compare_digest(projection["request_digest_sha256"], request.digest)
                or not hmac.compare_digest(
                    projection["idempotency_key_digest_sha256"], idempotency_digest
                )
                or projection["resources"] != list(request.resources)
            ):
                raise SecSWorkDenied("named_work_request_binding_mismatch")
            binding = credential_request_binding(request_json, idempotency_key)
            if not hmac.compare_digest(
                projection["credential_request_digest_sha256"], binding.request_digest_sha256
            ) or not hmac.compare_digest(
                projection["disclosure_digest_sha256"], binding.disclosure_digest_sha256
            ):
                raise SecSWorkDenied("named_work_request_binding_mismatch")
            projection_digest = hashlib.sha256(
                PROJECTION_DOMAIN + _canonical_json(projection)
            ).hexdigest()
            principal = _Principal(
                issuer=self.config.stable_issuer,
                audience=self.config.audience,
                actor_id=projection["actor_id"],
                session_id=projection["session_id"],
                correlation_id=f"dg:sha256:{projection_digest}",
            )
            safe_summary = {
                key: projection[key]
                for key in (
                    "operation",
                    "credential_request_digest_sha256",
                    "credential_digest_sha256",
                    "credential_presentation_digest_sha256",
                    "disclosure_digest_sha256",
                    "resources",
                    "actor_id",
                    "session_id",
                    "secs_context_id",
                    "request_digest_sha256",
                    "idempotency_key_digest_sha256",
                    "receiver_policy_id",
                    "receiver_policy_version",
                    "receiver_policy_digest_sha256",
                    "secs_verifier_key_id",
                    "issued_at",
                    "expires_at",
                )
            }
            safe_summary["secs_authority_projection_digest_sha256"] = projection_digest
            return _VerifiedWork(request, principal, idempotency_digest, safe_summary)
        except SecSWorkDenied:
            raise
        except (
            SecSIssueCreateDenied,
            InvalidWorkRequest,
            InvalidSignature,
            ValueError,
            TypeError,
            KeyError,
        ):
            raise SecSWorkDenied() from None
