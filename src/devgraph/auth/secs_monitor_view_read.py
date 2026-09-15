"""Exact proof-of-possession receiver for ``devgraph.monitor.view.read.v1``.

This module is deliberately separate from the bearer ``CredentialVerifier``
seam.  It verifies one secS-signed, short-lived browser session and one
ephemeral-page-key-signed HTTP request proof, then exposes only the safe
read-only monitor projection.  It never translates the proof into a generic
credential, scope, route, or caller-constructed authority context.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any
from unicodedata import category as unicode_category

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.scopes import CATEGORY_READ
from devgraph.auth.secs_issue_create import (
    SecSIssueCreateDenied,
    SecSVerifierKey,
    SecSVerifierKeyRegistry,
)
from devgraph.monitoring import build_monitor_snapshot
from devgraph.storage.base import GraphStorage

DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1 = "devgraph.monitor.view.read.v1"
DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1 = "devgraph://receiver-local"
DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1 = "http://127.0.0.1:8080"
DEVGRAPH_MONITOR_VIEW_READ_HOST_V1 = "127.0.0.1:8080"
DEVGRAPH_MONITOR_SESSION_SCHEMA_V1 = "secs-devgraph-monitor-session.v1"
DEVGRAPH_MONITOR_REQUEST_PROOF_SCHEMA_V1 = "devgraph-monitor-request-proof.v1"
DEVGRAPH_MONITOR_SIGNATURE_SUITE_V1 = "Ed25519"
DEVGRAPH_MONITOR_SESSION_SIGNATURE_DOMAIN_V1 = (
    b"secs-devgraph-monitor-session.v1/signature\x00"
)
DEVGRAPH_MONITOR_SESSION_DIGEST_DOMAIN_V1 = (
    b"secs-devgraph-monitor-session.v1/session\x00"
)
DEVGRAPH_MONITOR_REQUEST_PROOF_SIGNATURE_DOMAIN_V1 = (
    b"devgraph.monitor.view.read.v1/request-proof\x00"
)
DEVGRAPH_MONITOR_REQUEST_PROOF_DIGEST_DOMAIN_V1 = (
    b"devgraph.monitor.view.read.v1/request-proof-digest\x00"
)
DEVGRAPH_MONITOR_EMPTY_BODY_DIGEST_SHA256_V1 = hashlib.sha256(b"").hexdigest()
DEVGRAPH_MONITOR_PATH_QUERY_V1 = "/monitor/snapshot"
DEVGRAPH_MONITOR_SESSION_HEADER_V1 = "SecS-Devgraph-Monitor-Session"
DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1 = "SecS-Devgraph-Monitor-Proof"
DEVGRAPH_MONITOR_ORIGIN_HEADER_V1 = "SecS-Devgraph-Monitor-Origin"

DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1 = 9_007_199_254_740_991
DEVGRAPH_JSON_SAFE_INTEGER_MIN_V1 = -DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
DEVGRAPH_MONITOR_SESSION_MAX_SECONDS_V1 = 300
DEVGRAPH_MONITOR_REQUEST_CLOCK_SKEW_SECONDS_V1 = 30

_SESSION_MAX_BYTES = 8_192
_REQUEST_PROOF_MAX_BYTES = 4_096
_MAX_JSON_DEPTH = 8
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ACTOR_ID = re.compile(r"^pubkey:sha256:[0-9a-f]{64}$", re.ASCII)
_SECS_CONTEXT_ID = re.compile(r"^ctx:sha256:[0-9a-f]{64}$", re.ASCII)
_SAFE_LABEL_128 = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)
_SAFE_LABEL_256 = re.compile(r"^[A-Za-z0-9._:-]{1,256}$", re.ASCII)
_AUTHORITY_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$",
    re.ASCII,
)
_ED25519_FIELD_PRIME = (1 << 255) - 19
_ED25519_SCALAR_ORDER = (
    (1 << 252) + 27742317777372353535851937790883648493
)
_ED25519_SMALL_ORDER_ENCODINGS = frozenset(
    {
        bytes.fromhex("00" * 32),
        bytes.fromhex("01" + "00" * 31),
        bytes.fromhex(
            "26e8958fc2b227b045c3f489f2ef98f0"
            "d5dfac05d3c63339b13802886d53fc05"
        ),
        bytes.fromhex(
            "c7176a703d4dd84fba3c0b760d10670f"
            "2a2053fa2c39ccc64ec7fd7792ac037a"
        ),
        bytes.fromhex("ec" + "ff" * 30 + "7f"),
        bytes.fromhex("ed" + "ff" * 30 + "7f"),
        bytes.fromhex("ee" + "ff" * 30 + "7f"),
    }
)

_SESSION_FIELDS = frozenset(
    {
        "actor_id",
        "actor_signature_suite",
        "audience",
        "expires_at",
        "issued_at",
        "nonce",
        "operation",
        "origin",
        "page_public_key_base64url",
        "receiver_policy_digest_sha256",
        "receiver_policy_id",
        "receiver_policy_version",
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
_REQUEST_PROOF_FIELDS = frozenset(
    {
        "body_digest_sha256",
        "method",
        "nonce",
        "operation",
        "origin",
        "path_query",
        "schema",
        "schema_version",
        "session_digest_sha256",
        "session_id",
        "signature",
        "signature_suite",
        "timestamp",
    }
)


class SecSMonitorViewReadDenied(UnauthenticatedError):
    """One redaction-safe denial for every exact monitor proof failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("monitor proof denied")


@dataclass(frozen=True)
class SecSMonitorViewReadPolicyBinding:
    policy_id: str
    policy_version: int
    policy_digest_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_id, str)
            or _SAFE_LABEL_128.fullmatch(self.policy_id) is None
            or type(self.policy_version) is not int
            or not 1 <= self.policy_version <= DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
            or not isinstance(self.policy_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(self.policy_digest_sha256) is None
        ):
            raise ValueError("invalid monitor receiver policy binding")


class SecSMonitorViewReadReplayStore:
    """Exact-operation request-proof replay claim boundary."""

    @staticmethod
    def _validate_claim(
        session_digest_sha256: str,
        nonce: str,
        *,
        now: int,
        expires_at: int,
    ) -> None:
        if (
            not isinstance(session_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(session_digest_sha256) is None
        ):
            raise SecSMonitorViewReadDenied("invalid_monitor_replay_claim")
        _decode_base64url(
            nonce,
            encoded_length=16,
            decoded_length=12,
            reason="invalid_monitor_replay_claim",
        )
        _require_nonnegative_safe_integer(
            now,
            reason="invalid_monitor_replay_claim",
        )
        _require_nonnegative_safe_integer(
            expires_at,
            reason="invalid_monitor_replay_claim",
        )
        if expires_at <= now:
            raise SecSMonitorViewReadDenied("invalid_monitor_replay_claim")

    def claim(
        self,
        session_digest_sha256: str,
        nonce: str,
        *,
        now: int,
        expires_at: int,
    ) -> None:
        raise NotImplementedError

class SecSMonitorViewReadReplayCache(SecSMonitorViewReadReplayStore):
    """Thread-safe, expiry-pruned, fail-closed request-proof replay cache."""

    def __init__(self, *, maximum_entries: int = 4_096) -> None:
        if type(maximum_entries) is not int or not 1 <= maximum_entries <= 65_536:
            raise ValueError("invalid monitor replay cache capacity")
        self._maximum_entries = maximum_entries
        self._claims: dict[tuple[str, str], int] = {}
        self._lock = RLock()

    def claim(
        self,
        session_digest_sha256: str,
        nonce: str,
        *,
        now: int,
        expires_at: int,
    ) -> None:
        self._validate_claim(
            session_digest_sha256,
            nonce,
            now=now,
            expires_at=expires_at,
        )
        key = (session_digest_sha256, nonce)
        with self._lock:
            claims = {
                existing: expiry
                for existing, expiry in self._claims.items()
                if expiry > now
            }
            if key in claims:
                raise SecSMonitorViewReadDenied("monitor_request_replayed")
            if len(claims) >= self._maximum_entries:
                raise SecSMonitorViewReadDenied("monitor_replay_cache_full")
            claims[key] = expires_at
            self._claims = claims


@dataclass(frozen=True)
class SecSMonitorViewReadVerifierConfig:
    audience: str
    origin: str
    stable_issuer: str
    policy_binding: SecSMonitorViewReadPolicyBinding
    key_registry: SecSVerifierKeyRegistry
    replay_cache: SecSMonitorViewReadReplayStore
    clock: Callable[[], int]

    def __post_init__(self) -> None:
        if (
            self.audience != DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1
            or self.origin != DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1
            or not isinstance(self.stable_issuer, str)
            or _AUTHORITY_IDENTIFIER.fullmatch(self.stable_issuer) is None
            or not isinstance(
                self.policy_binding,
                SecSMonitorViewReadPolicyBinding,
            )
            or not isinstance(self.key_registry, SecSVerifierKeyRegistry)
            or not isinstance(self.replay_cache, SecSMonitorViewReadReplayStore)
            or not callable(self.clock)
        ):
            raise ValueError("invalid monitor verifier config")


@dataclass(frozen=True, repr=False)
class _VerifiedMonitorViewRead:
    actor_id: str
    session_id: str
    correlation_id: str
    issuer: str
    audience: str
    origin: str
    secs_context_id: str
    secs_verifier_key_id: str
    receiver_policy_id: str
    receiver_policy_version: int
    receiver_policy_digest_sha256: str
    page_public_key_digest_sha256: str
    session_digest_sha256: str
    request_proof_digest_sha256: str


class SecSMonitorViewReadVerifier:
    """Verify the exact secS session plus one page-key HTTP proof."""

    def __init__(self, config: SecSMonitorViewReadVerifierConfig) -> None:
        if not isinstance(config, SecSMonitorViewReadVerifierConfig):
            raise TypeError("invalid monitor verifier config")
        self._config = config

    def verify(
        self,
        *,
        signed_session_header: str,
        request_proof_header: str,
        method: str,
        path_query: str,
        origin: str,
        body: bytes,
    ) -> _VerifiedMonitorViewRead:
        signed_session, canonical_session = _decode_canonical_header_object(
            signed_session_header,
            maximum_bytes=_SESSION_MAX_BYTES,
            reason="invalid_monitor_session",
        )
        _require_exact_fields(
            signed_session,
            _SESSION_FIELDS,
            reason="invalid_monitor_session",
        )
        page_public_key = self._validate_session_shape(signed_session)
        now = self._read_clock()
        try:
            secs_key = self._config.key_registry.require_production_key(
                signed_session["secs_verifier_key_id"],
                now=now,
            )
        except SecSIssueCreateDenied:
            raise SecSMonitorViewReadDenied("untrusted_monitor_secs_key") from None
        self._verify_session_signature(signed_session, secs_key)
        self._validate_session_authority(signed_session, now=now)

        session_digest_sha256 = hashlib.sha256(
            DEVGRAPH_MONITOR_SESSION_DIGEST_DOMAIN_V1 + canonical_session
        ).hexdigest()
        request_proof, canonical_request_proof = _decode_canonical_header_object(
            request_proof_header,
            maximum_bytes=_REQUEST_PROOF_MAX_BYTES,
            reason="invalid_monitor_request_proof",
        )
        _require_exact_fields(
            request_proof,
            _REQUEST_PROOF_FIELDS,
            reason="invalid_monitor_request_proof",
        )
        self._validate_request_proof_shape(request_proof)
        self._validate_transport_binding(
            request_proof,
            signed_session=signed_session,
            session_digest_sha256=session_digest_sha256,
            method=method,
            path_query=path_query,
            origin=origin,
            body=body,
            now=now,
        )
        self._verify_request_proof_signature(request_proof, page_public_key)
        self._config.replay_cache.claim(
            session_digest_sha256,
            request_proof["nonce"],
            now=now,
            expires_at=signed_session["expires_at"],
        )

        request_proof_digest_sha256 = hashlib.sha256(
            DEVGRAPH_MONITOR_REQUEST_PROOF_DIGEST_DOMAIN_V1
            + canonical_request_proof
        ).hexdigest()
        correlation_id = (
            "dg:sha256:"
            + hashlib.sha256(
                DEVGRAPH_MONITOR_SESSION_DIGEST_DOMAIN_V1
                + canonical_session
                + canonical_request_proof
            ).hexdigest()
        )
        return _VerifiedMonitorViewRead(
            actor_id=signed_session["actor_id"],
            session_id=signed_session["session_id"],
            correlation_id=correlation_id,
            issuer=self._config.stable_issuer,
            audience=self._config.audience,
            origin=self._config.origin,
            secs_context_id=signed_session["secs_context_id"],
            secs_verifier_key_id=signed_session["secs_verifier_key_id"],
            receiver_policy_id=signed_session["receiver_policy_id"],
            receiver_policy_version=signed_session["receiver_policy_version"],
            receiver_policy_digest_sha256=signed_session[
                "receiver_policy_digest_sha256"
            ],
            page_public_key_digest_sha256=hashlib.sha256(page_public_key).hexdigest(),
            session_digest_sha256=session_digest_sha256,
            request_proof_digest_sha256=request_proof_digest_sha256,
        )

    def _read_clock(self) -> int:
        try:
            now = self._config.clock()
            _require_nonnegative_safe_integer(now, reason="clock_unavailable")
        except Exception:
            raise SecSMonitorViewReadDenied("clock_unavailable") from None
        return now

    @staticmethod
    def _validate_session_shape(session: Mapping[str, Any]) -> bytes:
        exact_strings = {
            "actor_signature_suite": DEVGRAPH_MONITOR_SIGNATURE_SUITE_V1,
            "operation": DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
            "schema": DEVGRAPH_MONITOR_SESSION_SCHEMA_V1,
            "secs_verifier_signature_suite": DEVGRAPH_MONITOR_SIGNATURE_SUITE_V1,
        }
        if any(session.get(field) != value for field, value in exact_strings.items()):
            raise SecSMonitorViewReadDenied("invalid_monitor_session")
        if type(session.get("schema_version")) is not int or session["schema_version"] != 1:
            raise SecSMonitorViewReadDenied("invalid_monitor_session")
        for field in ("issued_at", "expires_at", "receiver_policy_version"):
            _require_nonnegative_safe_integer(
                session.get(field),
                reason="invalid_monitor_session",
            )
        for field in (
            "receiver_policy_digest_sha256",
            "wallet_presentation_digest_sha256",
        ):
            value = session.get(field)
            if not isinstance(value, str) or _LOWER_HEX_64.fullmatch(value) is None:
                raise SecSMonitorViewReadDenied("invalid_monitor_session")
        if (
            not isinstance(session.get("actor_id"), str)
            or _ACTOR_ID.fullmatch(session["actor_id"]) is None
            or not isinstance(session.get("secs_context_id"), str)
            or _SECS_CONTEXT_ID.fullmatch(session["secs_context_id"]) is None
            or not isinstance(session.get("receiver_policy_id"), str)
            or _SAFE_LABEL_128.fullmatch(session["receiver_policy_id"]) is None
            or not isinstance(session.get("secs_verifier_key_id"), str)
            or _SAFE_LABEL_256.fullmatch(session["secs_verifier_key_id"]) is None
            or not _is_safe_receiver_value(session.get("audience"))
            or not _is_safe_receiver_value(session.get("origin"))
        ):
            raise SecSMonitorViewReadDenied("invalid_monitor_session")
        _decode_base64url(
            session.get("session_id"),
            encoded_length=22,
            decoded_length=16,
            reason="invalid_monitor_session",
        )
        _decode_base64url(
            session.get("nonce"),
            encoded_length=16,
            decoded_length=12,
            reason="invalid_monitor_session",
        )
        page_public_key = _decode_base64url(
            session.get("page_public_key_base64url"),
            encoded_length=43,
            decoded_length=32,
            reason="invalid_monitor_session",
        )
        if not _is_strict_ed25519_point_encoding(page_public_key):
            raise SecSMonitorViewReadDenied("invalid_monitor_session")
        _decode_base64url(
            session.get("secs_verifier_signature"),
            encoded_length=86,
            decoded_length=64,
            reason="invalid_monitor_session",
        )
        return page_public_key

    def _validate_session_authority(
        self,
        session: Mapping[str, Any],
        *,
        now: int,
    ) -> None:
        issued_at = session["issued_at"]
        expires_at = session["expires_at"]
        binding = self._config.policy_binding
        if (
            issued_at >= expires_at
            or expires_at - issued_at > DEVGRAPH_MONITOR_SESSION_MAX_SECONDS_V1
            or now < issued_at
            or now >= expires_at
            or session["audience"] != self._config.audience
            or session["origin"] != self._config.origin
            or session["receiver_policy_id"] != binding.policy_id
            or session["receiver_policy_version"] != binding.policy_version
            or not hmac.compare_digest(
                session["receiver_policy_digest_sha256"],
                binding.policy_digest_sha256,
            )
        ):
            raise SecSMonitorViewReadDenied("monitor_session_authority_mismatch")

    @staticmethod
    def _verify_session_signature(
        session: Mapping[str, Any],
        key: SecSVerifierKey,
    ) -> None:
        unsigned = dict(session)
        signature = _decode_base64url(
            unsigned.pop("secs_verifier_signature"),
            encoded_length=86,
            decoded_length=64,
            reason="invalid_monitor_session",
        )
        _verify_ed25519_signature(
            key.public_key,
            signature,
            DEVGRAPH_MONITOR_SESSION_SIGNATURE_DOMAIN_V1 + _canonical_json(unsigned),
            reason="invalid_monitor_session_signature",
        )

    @staticmethod
    def _validate_request_proof_shape(proof: Mapping[str, Any]) -> None:
        exact_strings = {
            "method": "GET",
            "operation": DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
            "path_query": DEVGRAPH_MONITOR_PATH_QUERY_V1,
            "schema": DEVGRAPH_MONITOR_REQUEST_PROOF_SCHEMA_V1,
            "signature_suite": DEVGRAPH_MONITOR_SIGNATURE_SUITE_V1,
        }
        if any(proof.get(field) != value for field, value in exact_strings.items()):
            raise SecSMonitorViewReadDenied("invalid_monitor_request_proof")
        if type(proof.get("schema_version")) is not int or proof["schema_version"] != 1:
            raise SecSMonitorViewReadDenied("invalid_monitor_request_proof")
        _require_nonnegative_safe_integer(
            proof.get("timestamp"),
            reason="invalid_monitor_request_proof",
        )
        for field in ("body_digest_sha256", "session_digest_sha256"):
            value = proof.get(field)
            if not isinstance(value, str) or _LOWER_HEX_64.fullmatch(value) is None:
                raise SecSMonitorViewReadDenied("invalid_monitor_request_proof")
        if not _is_safe_receiver_value(proof.get("origin")):
            raise SecSMonitorViewReadDenied("invalid_monitor_request_proof")
        _decode_base64url(
            proof.get("session_id"),
            encoded_length=22,
            decoded_length=16,
            reason="invalid_monitor_request_proof",
        )
        _decode_base64url(
            proof.get("nonce"),
            encoded_length=16,
            decoded_length=12,
            reason="invalid_monitor_request_proof",
        )
        _decode_base64url(
            proof.get("signature"),
            encoded_length=86,
            decoded_length=64,
            reason="invalid_monitor_request_proof",
        )

    def _validate_transport_binding(
        self,
        proof: Mapping[str, Any],
        *,
        signed_session: Mapping[str, Any],
        session_digest_sha256: str,
        method: str,
        path_query: str,
        origin: str,
        body: bytes,
        now: int,
    ) -> None:
        if (
            method != "GET"
            or path_query != DEVGRAPH_MONITOR_PATH_QUERY_V1
            or origin != self._config.origin
            or type(body) is not bytes
            or body != b""
            or proof["method"] != method
            or proof["path_query"] != path_query
            or proof["origin"] != origin
            or signed_session["origin"] != origin
            or proof["session_id"] != signed_session["session_id"]
            or not hmac.compare_digest(
                proof["session_digest_sha256"],
                session_digest_sha256,
            )
            or not hmac.compare_digest(
                proof["body_digest_sha256"],
                DEVGRAPH_MONITOR_EMPTY_BODY_DIGEST_SHA256_V1,
            )
        ):
            raise SecSMonitorViewReadDenied("monitor_request_binding_mismatch")
        timestamp = proof["timestamp"]
        if (
            timestamp < signed_session["issued_at"]
            or timestamp >= signed_session["expires_at"]
            or abs(now - timestamp)
            > DEVGRAPH_MONITOR_REQUEST_CLOCK_SKEW_SECONDS_V1
        ):
            raise SecSMonitorViewReadDenied("monitor_request_not_current")

    @staticmethod
    def _verify_request_proof_signature(
        proof: Mapping[str, Any],
        page_public_key: bytes,
    ) -> None:
        unsigned = dict(proof)
        signature = _decode_base64url(
            unsigned.pop("signature"),
            encoded_length=86,
            decoded_length=64,
            reason="invalid_monitor_request_proof",
        )
        _verify_ed25519_signature(
            page_public_key,
            signature,
            DEVGRAPH_MONITOR_REQUEST_PROOF_SIGNATURE_DOMAIN_V1
            + _canonical_json(unsigned),
            reason="invalid_monitor_request_signature",
        )


class SecSMonitorViewReadAdapter:
    """Execute only the exact authenticated, read-only monitor projection."""

    def __init__(
        self,
        *,
        verifier: SecSMonitorViewReadVerifier,
        storage: GraphStorage,
        audit_log: AuditLog,
    ) -> None:
        if not isinstance(verifier, SecSMonitorViewReadVerifier) or not isinstance(
            audit_log,
            AuditLog,
        ):
            raise TypeError("invalid monitor receiver dependencies")
        self._verifier = verifier
        self._storage = storage
        self._audit_log = audit_log

    def execute(
        self,
        *,
        signed_session_header: str,
        request_proof_header: str,
        method: str,
        path_query: str,
        origin: str,
        body: bytes,
    ) -> dict[str, Any]:
        grant = self._verifier.verify(
            signed_session_header=signed_session_header,
            request_proof_header=request_proof_header,
            method=method,
            path_query=path_query,
            origin=origin,
            body=body,
        )
        snapshot = build_monitor_snapshot(self._storage)
        self._audit_log.record(
            actor_id=grant.actor_id,
            session_id=grant.session_id,
            correlation_id=grant.correlation_id,
            category=CATEGORY_READ,
            operation=DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
            safe_summary={
                "audience": grant.audience,
                "issuer": grant.issuer,
                "operation": DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
                "origin": grant.origin,
                "page_public_key_digest_sha256": (
                    grant.page_public_key_digest_sha256
                ),
                "receiver_policy_digest_sha256": (
                    grant.receiver_policy_digest_sha256
                ),
                "receiver_policy_id": grant.receiver_policy_id,
                "receiver_policy_version": grant.receiver_policy_version,
                "request_proof_digest_sha256": grant.request_proof_digest_sha256,
                "secs_context_id": grant.secs_context_id,
                "secs_verifier_key_id": grant.secs_verifier_key_id,
                "session_digest_sha256": grant.session_digest_sha256,
            },
        )
        return snapshot


def _decode_canonical_header_object(
    header: object,
    *,
    maximum_bytes: int,
    reason: str,
) -> tuple[dict[str, Any], bytes]:
    if (
        not isinstance(header, str)
        or not header
        or len(header) > ((maximum_bytes * 4 + 2) // 3)
        or _BASE64URL.fullmatch(header) is None
    ):
        raise SecSMonitorViewReadDenied(reason)
    try:
        raw = base64.b64decode(
            header + "=" * (-len(header) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise SecSMonitorViewReadDenied(reason) from None
    if (
        not raw
        or len(raw) > maximum_bytes
        or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != header
    ):
        raise SecSMonitorViewReadDenied(reason)
    value = _strict_json_object(raw, maximum_bytes=maximum_bytes, reason=reason)
    canonical = _canonical_json(value)
    if raw != canonical:
        raise SecSMonitorViewReadDenied(reason)
    return value, canonical


def _strict_json_object(
    raw_json: bytes,
    *,
    maximum_bytes: int,
    reason: str,
) -> dict[str, Any]:
    if type(raw_json) is not bytes or len(raw_json) > maximum_bytes:
        raise SecSMonitorViewReadDenied(reason)

    def reject_float(_value: str) -> Any:
        raise ValueError("floating-point JSON is not accepted")

    def parse_integer(value: str) -> int:
        parsed = int(value)
        if not DEVGRAPH_JSON_SAFE_INTEGER_MIN_V1 <= parsed <= (
            DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
        ):
            raise ValueError("JSON integer is outside the safe range")
        return parsed

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    try:
        text = raw_json.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError("JSON BOM is not accepted")
        value = json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_int=parse_integer,
            parse_float=reject_float,
            parse_constant=reject_float,
        )
        _require_json_depth(value)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise SecSMonitorViewReadDenied(reason) from None
    if not isinstance(value, dict):
        raise SecSMonitorViewReadDenied(reason)
    return value


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise SecSMonitorViewReadDenied("invalid_canonical_json") from None


def _decode_base64url(
    value: object,
    *,
    encoded_length: int,
    decoded_length: int,
    reason: str,
) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != encoded_length
        or _BASE64URL.fullmatch(value) is None
        or "=" in value
    ):
        raise SecSMonitorViewReadDenied(reason)
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise SecSMonitorViewReadDenied(reason) from None
    if (
        len(decoded) != decoded_length
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise SecSMonitorViewReadDenied(reason)
    return decoded


def _verify_ed25519_signature(
    public_key: bytes,
    signature: bytes,
    preimage: bytes,
    *,
    reason: str,
) -> None:
    if (
        not _is_strict_ed25519_point_encoding(public_key)
        or not _is_strict_ed25519_point_encoding(signature[:32])
        or int.from_bytes(signature[32:], "little") >= _ED25519_SCALAR_ORDER
    ):
        raise SecSMonitorViewReadDenied(reason)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, preimage)
    except (InvalidSignature, ValueError):
        raise SecSMonitorViewReadDenied(reason) from None


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: frozenset[str],
    *,
    reason: str,
) -> None:
    if set(value) != fields:
        raise SecSMonitorViewReadDenied(reason)


def _require_json_depth(value: object, *, maximum: int = _MAX_JSON_DEPTH) -> None:
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > maximum:
            raise ValueError("JSON nesting is too deep")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _is_safe_receiver_value(value: object) -> bool:
    if not isinstance(value, str) or value.strip() == "":
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= 256 and not any(
        unicode_category(character) == "Cc" for character in value
    )


def _is_strict_ed25519_point_encoding(value: bytes) -> bool:
    if type(value) is not bytes or len(value) != 32:
        return False
    normalized = value[:31] + bytes((value[31] & 0x7F,))
    return (
        int.from_bytes(normalized, "little") < _ED25519_FIELD_PRIME
        and normalized not in _ED25519_SMALL_ORDER_ENCODINGS
    )


def _require_nonnegative_safe_integer(value: object, *, reason: str) -> int:
    if (
        type(value) is not int
        or value < 0
        or value > DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
    ):
        raise SecSMonitorViewReadDenied(reason)
    return value
