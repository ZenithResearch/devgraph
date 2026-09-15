"""Receiver for the separate, closed named Work authority v1 contract."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devgraph.arena_requests import ArenaRequest
from devgraph.arenas import ArenaMutations
from devgraph.auth.enforcement import AuditLog
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
from devgraph.events.outbox import EventOutbox
from devgraph.named_requests import parse_named_request
from devgraph.named_work import NamedWorkMutations
from devgraph.storage.base import EventOutboxStorage
from devgraph.work_requests import InvalidWorkRequest, WorkRequest

SCHEMA = "secs-devgraph-work-authority.v1"
SIGNATURE_DOMAIN = b"secs-devgraph-work-authority.v1/signature\x00"
PROJECTION_DOMAIN = b"secs-devgraph-work-authority.v1/projection\x00"
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
        "wallet_presentation_digest_sha256",
    }
)


class SecSWorkDenied(RuntimeError):
    def __init__(self, reason="named_work_authority_denied"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, repr=False)
class _Principal:
    issuer: str
    audience: str
    actor_id: str
    session_id: str
    correlation_id: str


@dataclass(frozen=True, repr=False)
class _VerifiedWork:
    request: WorkRequest | ArenaRequest
    principal: _Principal
    idempotency_digest: str
    safe_summary: dict[str, Any]


class SecSWorkVerifier:
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
                "replay_scope": "session:operation:nonce",
                "audience": self.config.audience,
            }
            if any(projection[key] != value for key, value in exact.items()):
                raise SecSWorkDenied()
            if type(projection["schema_version"]) is not int or projection["schema_version"] != 1:
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
                        "wallet_presentation_digest_sha256",
                        "receiver_policy_digest_sha256",
                    )
                ],
            ):
                if (
                    not isinstance(projection[field], str)
                    or pattern.fullmatch(projection[field]) is None
                ):
                    raise SecSWorkDenied()
            for field, encoded, decoded in (("session_id", 22, 16), ("nonce", 16, 12)):
                _decode_base64url(
                    projection[field],
                    encoded_length=encoded,
                    decoded_length=decoded,
                    reason="invalid_work_projection",
                )
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


class SecSWorkAdapter:
    def __init__(
        self, *, storage: EventOutboxStorage, verifier: SecSWorkVerifier, audit_log: AuditLog
    ):
        self.storage, self.verifier, self.audit_log = storage, verifier, audit_log
        self.mutations = NamedWorkMutations(storage)
        self.arena_mutations = ArenaMutations(storage)
        self.outbox = EventOutbox(storage)

    def execute(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str):
        verified = self.verifier.verify(
            request_json=request_json,
            projection_json=projection_json,
            idempotency_key=idempotency_key,
        )
        request = verified.request
        subject_kind, subject_id = (
            ("Issue", request.payload["issue_id"])
            if request.operation == "convert"
            else (request.kind, request.id)
        )

        def audit_result(receipt, duplicate):
            self.audit_log.record(
                actor_id=verified.principal.actor_id,
                session_id=verified.principal.session_id,
                correlation_id=verified.principal.correlation_id,
                category="write",
                operation=request.authority_operation,
                safe_summary={
                    **verified.safe_summary,
                    "receipt_id": receipt.id,
                    "duplicate": duplicate,
                },
            )

        with self.audit_log.transaction():
            with self.storage.work_mutation_transaction():
                # Queueing for the database lock must not extend the proof lifetime.
                self.verifier.verify(
                    request_json=request_json,
                    projection_json=projection_json,
                    idempotency_key=idempotency_key,
                )
                work, receipt = self.outbox.record_named_work_v1_with_receipt(
                    principal=verified.principal,
                    operation=request.authority_operation,
                    subject_label=subject_kind,
                    subject_id=subject_id,
                    idempotency_key=idempotency_key,
                    request_digest_sha256=request.digest,
                    idempotency_key_digest_sha256=verified.idempotency_digest,
                    summary=verified.safe_summary,
                    mutation=lambda _: (
                        self.arena_mutations
                        if isinstance(request, ArenaRequest) else self.mutations
                    ).execute(request),
                    on_result=audit_result,
                )
        return work, receipt, work is None
