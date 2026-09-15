"""Exact secS consumer for ``devgraph.issue.create.v1``.

This module is deliberately operation-specific. It does not expose a generic
credential verifier, scope mapper, route selector, or caller-constructed
authority context. The adapter verifies one portable secS projection and then
opens one graph-owned, single-use session whose only mutation is
``create_issue()``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import RLock
from typing import Any
from unicodedata import category as unicode_category
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import ForbiddenError
from devgraph.auth.scopes import CATEGORY_WRITE, SCOPE_WRITE
from devgraph.events.model import EventReceipt
from devgraph.events.outbox import EventOutbox
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.validation import validate_work_object_id
from devgraph.model.work import Issue
from devgraph.storage.base import EventOutboxStorage

DEVGRAPH_ISSUE_CREATE_OPERATION_V1 = "devgraph.issue.create.v1"
DEVGRAPH_ISSUE_CREATE_SCHEMA_V1 = "secs-devgraph-authority.v1"
DEVGRAPH_ISSUE_CREATE_REPLAY_SCOPE_V1 = "session:operation:nonce"
DEVGRAPH_ISSUE_CREATE_SIGNATURE_SUITE_V1 = "Ed25519"
DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 = b"devgraph.issue.create.request.v1\x00"
DEVGRAPH_ISSUE_CREATE_SIGNATURE_DOMAIN_V1 = (
    b"secs-devgraph-authority.v1/signature\x00"
)
DEVGRAPH_ISSUE_CREATE_PROJECTION_DOMAIN_V1 = (
    b"secs-devgraph-authority.v1/projection\x00"
)
DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1 = 9_007_199_254_740_991
DEVGRAPH_JSON_SAFE_INTEGER_MIN_V1 = -DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1

_REQUEST_MAX_BYTES = 131_072
_CANONICAL_REQUEST_MAX_BYTES = 65_536
_PROJECTION_MAX_BYTES = 16_384
_REGISTRY_MAX_BYTES = 262_144
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{16,128}$", re.ASCII)
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ACTOR_ID = re.compile(r"^pubkey:sha256:[0-9a-f]{64}$", re.ASCII)
_SECS_CONTEXT_ID = re.compile(r"^ctx:sha256:[0-9a-f]{64}$", re.ASCII)
_AUTHORITY_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$",
    re.ASCII,
)
_SAFE_LABEL_128 = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)
_SAFE_LABEL_256 = re.compile(r"^[A-Za-z0-9._:-]{1,256}$", re.ASCII)
_ISSUE_RESOURCE = re.compile(
    r"^Issue/[a-z0-9](?:[a-z0-9-]{0,254}[a-z0-9])?$",
    re.ASCII,
)
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)
_MAX_JSON_DEPTH = 16
_ED25519_FIELD_PRIME = (1 << 255) - 19
_ED25519_SCALAR_ORDER = (1 << 252) + 27742317777372353535851937790883648493
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

_REQUEST_FIELDS = frozenset(
    {
        "artifact_ids",
        "description",
        "external_link_ids",
        "id",
        "kind",
        "priority",
        "title",
    }
)
_REQUEST_REQUIRED_FIELDS = frozenset({"id", "kind", "title"})
_PROJECTION_FIELDS = frozenset(
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
        "resource",
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
_REGISTRY_FIELDS = frozenset({"keys", "schema", "schema_version"})
_REGISTRY_KEY_REQUIRED_FIELDS = frozenset(
    {
        "algorithm",
        "key_id",
        "production_authority",
        "public_key_base64url",
        "status",
    }
)
_REGISTRY_KEY_OPTIONAL_FIELDS = frozenset(
    {"not_before", "not_after", "revoked_at", "replaced_by"}
)


class SecSIssueCreateDenied(RuntimeError):
    """Bounded, redaction-safe denial for the exact secS consumer."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SecSIssueCreatePolicyBinding:
    policy_id: str
    policy_version: int
    policy_digest_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_id, str)
            or _SAFE_LABEL_128.fullmatch(self.policy_id) is None
        ):
            raise ValueError("invalid receiver policy binding")
        _require_nonnegative_safe_integer(
            self.policy_version,
            reason="invalid receiver policy binding",
        )
        if (
            self.policy_version == 0
            or not isinstance(self.policy_digest_sha256, str)
            or _LOWER_HEX_64.fullmatch(self.policy_digest_sha256) is None
        ):
            raise ValueError("invalid receiver policy binding")


class SecSIssueCreatePolicyRegistry:
    """Duplicate-safe receiver-owned policy-binding registry."""

    def __init__(self, bindings: Iterable[SecSIssueCreatePolicyBinding]) -> None:
        self._bindings: dict[
            tuple[str, int], SecSIssueCreatePolicyBinding
        ] = {}
        self._duplicates: set[tuple[str, int]] = set()
        for binding in bindings:
            if not isinstance(binding, SecSIssueCreatePolicyBinding):
                raise TypeError("invalid receiver policy binding")
            key = (binding.policy_id, binding.policy_version)
            if key in self._bindings:
                self._duplicates.add(key)
            self._bindings[key] = binding
        if not self._bindings:
            raise ValueError("empty receiver policy registry")

    def require_binding(
        self,
        policy_id: str,
        policy_version: int,
        policy_digest_sha256: str,
    ) -> SecSIssueCreatePolicyBinding:
        key = (policy_id, policy_version)
        binding = self._bindings.get(key)
        if (
            key in self._duplicates
            or binding is None
            or not hmac.compare_digest(
                binding.policy_digest_sha256,
                policy_digest_sha256,
            )
        ):
            raise SecSIssueCreateDenied("secs_receiver_policy_mismatch")
        return binding


@dataclass(frozen=True)
class SecSVerifierKey:
    key_id: str
    public_key: bytes
    algorithm: str = "ed25519"
    status: str = "active"
    production_authority: bool = True
    not_before: int | None = None
    not_after: int | None = None
    revoked_at: int | None = None
    replaced_by: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key_id, str)
            or _SAFE_LABEL_256.fullmatch(self.key_id) is None
            or type(self.public_key) is not bytes
            or len(self.public_key) != 32
            or not isinstance(self.algorithm, str)
            or not isinstance(self.status, str)
            or type(self.production_authority) is not bool
        ):
            raise ValueError("invalid secS verifier key")
        for value in (self.not_before, self.not_after, self.revoked_at):
            if value is not None:
                _require_nonnegative_safe_integer(
                    value,
                    reason="invalid secS verifier key",
                )
        if self.replaced_by is not None and (
            not isinstance(self.replaced_by, str)
            or _SAFE_LABEL_256.fullmatch(self.replaced_by) is None
        ):
            raise ValueError("invalid secS verifier key")


class SecSVerifierKeyRegistry:
    """Receiver-owned secS production-authority key registry."""

    def __init__(self, keys: Iterable[SecSVerifierKey]) -> None:
        self._keys: dict[str, SecSVerifierKey] = {}
        self._duplicates: set[str] = set()
        for key in keys:
            if not isinstance(key, SecSVerifierKey):
                raise TypeError("invalid secS verifier key")
            if key.key_id in self._keys:
                self._duplicates.add(key.key_id)
            self._keys[key.key_id] = key
        if not self._keys:
            raise ValueError("empty secS verifier key registry")

    @classmethod
    def from_json(cls, registry_json: bytes) -> SecSVerifierKeyRegistry:
        try:
            registry = _strict_json_object(
                registry_json,
                maximum_bytes=_REGISTRY_MAX_BYTES,
                reason="invalid secS verifier key registry",
            )
        except SecSIssueCreateDenied as exc:
            raise ValueError("invalid secS verifier key registry") from exc
        if set(registry) != _REGISTRY_FIELDS:
            raise ValueError("invalid secS verifier key registry")
        if (
            registry.get("schema") != "secs-public-verifier-key-registry.v1"
            or type(registry.get("schema_version")) is not int
            or registry["schema_version"] != 1
            or not isinstance(registry.get("keys"), list)
            or not 1 <= len(registry["keys"]) <= 256
        ):
            raise ValueError("invalid secS verifier key registry")
        keys: list[SecSVerifierKey] = []
        allowed_fields = _REGISTRY_KEY_REQUIRED_FIELDS | _REGISTRY_KEY_OPTIONAL_FIELDS
        for raw_key in registry["keys"]:
            if (
                not isinstance(raw_key, dict)
                or not _REGISTRY_KEY_REQUIRED_FIELDS.issubset(raw_key)
                or not set(raw_key).issubset(allowed_fields)
            ):
                raise ValueError("invalid secS verifier key registry")
            try:
                public_key = _decode_base64url(
                    raw_key["public_key_base64url"],
                    encoded_length=43,
                    decoded_length=32,
                    reason="invalid secS verifier key registry",
                )
                keys.append(
                    SecSVerifierKey(
                        key_id=raw_key["key_id"],
                        public_key=public_key,
                        algorithm=raw_key["algorithm"],
                        status=raw_key["status"],
                        production_authority=raw_key["production_authority"],
                        not_before=raw_key.get("not_before"),
                        not_after=raw_key.get("not_after"),
                        revoked_at=raw_key.get("revoked_at"),
                        replaced_by=raw_key.get("replaced_by"),
                    )
                )
            except (TypeError, ValueError, SecSIssueCreateDenied) as exc:
                raise ValueError("invalid secS verifier key registry") from exc
        return cls(keys)

    def require_production_key(self, key_id: str, *, now: int) -> SecSVerifierKey:
        if key_id in self._duplicates:
            raise SecSIssueCreateDenied("unknown_secs_verifier_key")
        key = self._keys.get(key_id)
        if key is None:
            raise SecSIssueCreateDenied("unknown_secs_verifier_key")
        if (
            key.algorithm != "ed25519"
            or not key.production_authority
            or key.status != "active"
            or not _is_strict_ed25519_point_encoding(key.public_key)
        ):
            raise SecSIssueCreateDenied("untrusted_secs_verifier_key")
        if key.not_before is not None and now < key.not_before:
            raise SecSIssueCreateDenied("secs_verifier_key_not_yet_valid")
        if key.not_after is not None and now >= key.not_after:
            raise SecSIssueCreateDenied("secs_verifier_key_expired")
        if key.revoked_at is not None and now >= key.revoked_at:
            raise SecSIssueCreateDenied("secs_verifier_key_revoked")
        return key


@dataclass(frozen=True)
class SecSIssueCreateVerifierConfig:
    audience: str
    stable_issuer: str
    policy_registry: SecSIssueCreatePolicyRegistry
    key_registry: SecSVerifierKeyRegistry
    clock: Callable[[], int]

    def __post_init__(self) -> None:
        if (
            not _is_safe_receiver_value(self.audience)
            or not isinstance(self.stable_issuer, str)
            or _AUTHORITY_IDENTIFIER.fullmatch(self.stable_issuer) is None
            or not isinstance(
                self.policy_registry,
                SecSIssueCreatePolicyRegistry,
            )
            or not isinstance(self.key_registry, SecSVerifierKeyRegistry)
            or not callable(self.clock)
        ):
            raise ValueError("invalid secS Issue-create verifier config")


@dataclass(frozen=True, repr=False)
class _SecSIssueCreatePrincipal:
    issuer: str
    audience: str
    actor_id: str
    session_id: str
    correlation_id: str
    scope: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, repr=False)
class _VerifiedIssueCreate:
    issue: Issue
    principal: _SecSIssueCreatePrincipal
    request_digest_sha256: str
    idempotency_key_digest_sha256: str
    safe_summary: dict[str, Any]


class SecSIssueCreateVerifier:
    """Verify one exact portable secS authority projection."""

    def __init__(self, config: SecSIssueCreateVerifierConfig) -> None:
        if not isinstance(config, SecSIssueCreateVerifierConfig):
            raise TypeError("invalid secS Issue-create verifier config")
        self._config = config

    def verify(
        self,
        *,
        request_json: bytes,
        idempotency_key: str,
        signed_projection_json: bytes,
    ) -> _VerifiedIssueCreate:
        projection = _strict_json_object(
            signed_projection_json,
            maximum_bytes=_PROJECTION_MAX_BYTES,
            reason="invalid_secs_authority_projection",
        )
        _require_exact_fields(
            projection,
            _PROJECTION_FIELDS,
            reason="invalid_secs_authority_projection",
        )
        self._validate_projection_shape(projection)
        now = self._read_clock()
        key = self._config.key_registry.require_production_key(
            projection["secs_verifier_key_id"],
            now=now,
        )
        self._verify_projection_signature(projection, key)
        self._validate_projection_authority(projection, now=now)

        issue, canonical_request = _parse_issue_create_request(request_json)
        request_digest_sha256 = hashlib.sha256(
            DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 + canonical_request
        ).hexdigest()
        if not hmac.compare_digest(
            request_digest_sha256,
            projection["request_digest_sha256"],
        ):
            raise SecSIssueCreateDenied("devgraph_request_digest_mismatch")

        idempotency_key_digest_sha256 = _digest_idempotency_key(idempotency_key)
        if not hmac.compare_digest(
            idempotency_key_digest_sha256,
            projection["idempotency_key_digest_sha256"],
        ):
            raise SecSIssueCreateDenied("devgraph_idempotency_digest_mismatch")

        resource = f"Issue/{issue.id}"
        if projection["resource"] != resource:
            raise SecSIssueCreateDenied("devgraph_resource_mismatch")

        full_projection = _canonical_json(projection)
        projection_digest_sha256 = hashlib.sha256(
            DEVGRAPH_ISSUE_CREATE_PROJECTION_DOMAIN_V1 + full_projection
        ).hexdigest()
        correlation_id = f"dg:sha256:{projection_digest_sha256}"
        principal = _SecSIssueCreatePrincipal(
            issuer=self._config.stable_issuer,
            audience=self._config.audience,
            actor_id=projection["actor_id"],
            session_id=projection["session_id"],
            correlation_id=correlation_id,
            scope=SCOPE_WRITE,
            issued_at=projection["issued_at"],
            expires_at=projection["expires_at"],
        )
        safe_summary: dict[str, Any] = {
            "operation": DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
            "resource": resource,
            "actor_id": projection["actor_id"],
            "session_id": projection["session_id"],
            "secs_context_id": projection["secs_context_id"],
            "request_digest_sha256": request_digest_sha256,
            "idempotency_key_digest_sha256": idempotency_key_digest_sha256,
            "receiver_policy_id": projection["receiver_policy_id"],
            "receiver_policy_version": projection["receiver_policy_version"],
            "receiver_policy_digest_sha256": projection[
                "receiver_policy_digest_sha256"
            ],
            "secs_verifier_key_id": projection["secs_verifier_key_id"],
            "secs_authority_projection_digest_sha256": projection_digest_sha256,
        }
        return _VerifiedIssueCreate(
            issue=issue,
            principal=principal,
            request_digest_sha256=request_digest_sha256,
            idempotency_key_digest_sha256=idempotency_key_digest_sha256,
            safe_summary=safe_summary,
        )

    def _read_clock(self) -> int:
        try:
            now = self._config.clock()
            _require_nonnegative_safe_integer(now, reason="clock_unavailable")
        except Exception:
            raise SecSIssueCreateDenied("clock_unavailable") from None
        return now

    def _validate_projection_shape(self, projection: Mapping[str, Any]) -> None:
        exact_strings = {
            "schema": DEVGRAPH_ISSUE_CREATE_SCHEMA_V1,
            "operation": DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
            "actor_signature_suite": DEVGRAPH_ISSUE_CREATE_SIGNATURE_SUITE_V1,
            "secs_verifier_signature_suite": (
                DEVGRAPH_ISSUE_CREATE_SIGNATURE_SUITE_V1
            ),
            "replay_scope": DEVGRAPH_ISSUE_CREATE_REPLAY_SCOPE_V1,
        }
        if any(projection.get(field) != value for field, value in exact_strings.items()):
            raise SecSIssueCreateDenied("invalid_secs_authority_projection")
        if (
            type(projection.get("schema_version")) is not int
            or projection["schema_version"] != 1
        ):
            raise SecSIssueCreateDenied("invalid_secs_authority_projection")
        for field in ("issued_at", "expires_at", "receiver_policy_version"):
            _require_nonnegative_safe_integer(
                projection.get(field),
                reason="invalid_secs_authority_projection",
            )
        for field in (
            "idempotency_key_digest_sha256",
            "receiver_policy_digest_sha256",
            "request_digest_sha256",
            "wallet_presentation_digest_sha256",
        ):
            value = projection.get(field)
            if not isinstance(value, str) or _LOWER_HEX_64.fullmatch(value) is None:
                raise SecSIssueCreateDenied("invalid_secs_authority_projection")
        if (
            not isinstance(projection.get("actor_id"), str)
            or _ACTOR_ID.fullmatch(projection["actor_id"]) is None
            or not isinstance(projection.get("secs_context_id"), str)
            or _SECS_CONTEXT_ID.fullmatch(projection["secs_context_id"]) is None
        ):
            raise SecSIssueCreateDenied("invalid_secs_authority_projection")
        if (
            not _is_safe_receiver_value(projection.get("audience"))
            or not isinstance(projection.get("receiver_policy_id"), str)
            or _SAFE_LABEL_128.fullmatch(projection["receiver_policy_id"]) is None
            or not isinstance(projection.get("resource"), str)
            or _ISSUE_RESOURCE.fullmatch(projection["resource"]) is None
            or not isinstance(projection.get("secs_verifier_key_id"), str)
            or _SAFE_LABEL_256.fullmatch(projection["secs_verifier_key_id"])
            is None
        ):
            raise SecSIssueCreateDenied("invalid_secs_authority_projection")
        _decode_base64url(
            projection.get("session_id"),
            encoded_length=22,
            decoded_length=16,
            reason="invalid_secs_authority_projection",
        )
        _decode_base64url(
            projection.get("nonce"),
            encoded_length=16,
            decoded_length=12,
            reason="invalid_secs_authority_projection",
        )
        _decode_base64url(
            projection.get("secs_verifier_signature"),
            encoded_length=86,
            decoded_length=64,
            reason="invalid_secs_authority_projection",
        )

    def _validate_projection_authority(
        self,
        projection: Mapping[str, Any],
        *,
        now: int,
    ) -> None:
        issued_at = projection["issued_at"]
        expires_at = projection["expires_at"]
        if (
            issued_at >= expires_at
            or expires_at - issued_at > 60
            or now < issued_at
            or now >= expires_at
        ):
            raise SecSIssueCreateDenied("secs_authority_not_current")
        if projection["audience"] != self._config.audience:
            raise SecSIssueCreateDenied("secs_receiver_policy_mismatch")
        self._config.policy_registry.require_binding(
            projection["receiver_policy_id"],
            projection["receiver_policy_version"],
            projection["receiver_policy_digest_sha256"],
        )

    @staticmethod
    def _verify_projection_signature(
        projection: Mapping[str, Any],
        key: SecSVerifierKey,
    ) -> None:
        unsigned_projection = dict(projection)
        signature_text = unsigned_projection.pop("secs_verifier_signature")
        signature = _decode_base64url(
            signature_text,
            encoded_length=86,
            decoded_length=64,
            reason="invalid_secs_authority_projection",
        )
        if (
            not _is_strict_ed25519_point_encoding(signature[:32])
            or int.from_bytes(signature[32:], "little") >= _ED25519_SCALAR_ORDER
        ):
            raise SecSIssueCreateDenied("invalid_secs_authority_signature")
        preimage = (
            DEVGRAPH_ISSUE_CREATE_SIGNATURE_DOMAIN_V1
            + _canonical_json(unsigned_projection)
        )
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                signature,
                preimage,
            )
        except (InvalidSignature, ValueError):
            raise SecSIssueCreateDenied("invalid_secs_authority_signature") from None


@dataclass(frozen=True)
class SecSIssueCreateResult:
    issue: Issue | None
    receipt: EventReceipt
    duplicate: bool


@dataclass(frozen=True, repr=False)
class _IssueCreateSessionState:
    grant: _VerifiedIssueCreate


class _SecSIssueCreateGraph:
    """Graph owner for exact, single-use Issue-create sessions."""

    def __init__(self, storage: EventOutboxStorage, audit_log: AuditLog) -> None:
        if not isinstance(audit_log, AuditLog):
            raise TypeError("invalid secS Issue-create graph dependencies")
        self._repository = WorkObjectRepository(storage)
        self._audit_log = audit_log
        self.__sessions: WeakKeyDictionary[
            _SecSIssueCreateSession, _IssueCreateSessionState
        ] = WeakKeyDictionary()
        self.__sessions_lock = RLock()

    def _open_session(
        self,
        grant: _VerifiedIssueCreate,
        constructor: object,
    ) -> _SecSIssueCreateSession:
        if constructor is not _ISSUE_CREATE_ADAPTER:
            raise TypeError("Issue-create sessions are adapter-owned")
        session = _SecSIssueCreateSession(self, _ISSUE_CREATE_SESSION)
        with self.__sessions_lock:
            self.__sessions[session] = _IssueCreateSessionState(grant)
        return session

    def _state(self, session: _SecSIssueCreateSession) -> _IssueCreateSessionState:
        with self.__sessions_lock:
            try:
                return self.__sessions[session]
            except KeyError:
                raise ForbiddenError("invalid Issue-create session") from None

    def _consume(self, session: _SecSIssueCreateSession) -> Issue:
        with self.__sessions_lock:
            try:
                state = self.__sessions.pop(session)
            except KeyError:
                raise ForbiddenError("invalid Issue-create session") from None
        issue = self._repository.create(state.grant.issue)
        return issue

    def _transaction(
        self,
        session: _SecSIssueCreateSession,
    ) -> AbstractContextManager[None]:
        self._state(session)
        return self._audit_log.transaction()

    def _audit_result(
        self,
        grant: _VerifiedIssueCreate,
        receipt: EventReceipt,
        *,
        duplicate: bool,
    ) -> None:
        summary = {
            **grant.safe_summary,
            "duplicate": duplicate,
            "persisted_receipt_status": receipt.status.value,
            "receipt_id": receipt.id,
        }
        self._audit_log.record(
            actor_id=grant.principal.actor_id,
            session_id=grant.principal.session_id,
            correlation_id=grant.principal.correlation_id,
            category=CATEGORY_WRITE,
            operation=DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
            safe_summary=summary,
        )

    def _discard(self, session: _SecSIssueCreateSession) -> None:
        with self.__sessions_lock:
            self.__sessions.pop(session, None)


_ISSUE_CREATE_ADAPTER = object()
_ISSUE_CREATE_SESSION = object()


class _SecSIssueCreateSession:
    """Immutable graph-registered session exposing only ``create_issue``."""

    __slots__ = ("__weakref__", "_graph", "__sealed")

    def __init__(self, graph: _SecSIssueCreateGraph, constructor: object) -> None:
        if constructor is not _ISSUE_CREATE_SESSION:
            raise TypeError("Issue-create sessions are graph-owned")
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_SecSIssueCreateSession__sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_SecSIssueCreateSession__sealed", False):
            raise AttributeError("Issue-create sessions are immutable")
        object.__setattr__(self, name, value)

    def create_issue(self) -> Issue:
        return self._graph._consume(self)


class SecSIssueCreateAdapter:
    """Verify and execute exactly one secS-authorized Issue create."""

    def __init__(
        self,
        *,
        verifier: SecSIssueCreateVerifier,
        storage: EventOutboxStorage,
        audit_log: AuditLog,
        receipt_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(verifier, SecSIssueCreateVerifier) or not isinstance(
            audit_log,
            AuditLog,
        ):
            raise TypeError("invalid secS Issue-create adapter dependencies")
        self._verifier = verifier
        self._graph = _SecSIssueCreateGraph(storage, audit_log)
        self._outbox = EventOutbox(
            storage,
            receipt_id_factory=receipt_id_factory,
        )

    def execute(
        self,
        *,
        request_json: bytes,
        idempotency_key: str,
        signed_projection_json: bytes,
    ) -> SecSIssueCreateResult:
        grant = self._verifier.verify(
            request_json=request_json,
            idempotency_key=idempotency_key,
            signed_projection_json=signed_projection_json,
        )
        session = self._graph._open_session(grant, _ISSUE_CREATE_ADAPTER)
        try:
            with self._graph._transaction(session):
                issue, receipt = self._outbox.record_secs_issue_create_v1_with_receipt(
                    principal=grant.principal,
                    subject_id=grant.issue.id,
                    idempotency_key=idempotency_key,
                    request_digest_sha256=grant.request_digest_sha256,
                    idempotency_key_digest_sha256=(
                        grant.idempotency_key_digest_sha256
                    ),
                    summary=grant.safe_summary,
                    mutation=lambda _storage: session.create_issue(),
                    on_result=lambda result_receipt, duplicate: (
                        self._graph._audit_result(
                            grant,
                            result_receipt,
                            duplicate=duplicate,
                        )
                    ),
                )
        finally:
            self._graph._discard(session)
        if issue is not None and not isinstance(issue, Issue):
            raise RuntimeError("invalid Issue-create mutation result")
        return SecSIssueCreateResult(
            issue=issue,
            receipt=receipt,
            duplicate=issue is None,
        )


def _parse_issue_create_request(request_json: bytes) -> tuple[Issue, bytes]:
    request = _strict_json_object(
        request_json,
        maximum_bytes=_REQUEST_MAX_BYTES,
        reason="invalid_devgraph_issue_create_request",
    )
    if (
        not _REQUEST_REQUIRED_FIELDS.issubset(request)
        or not set(request).issubset(_REQUEST_FIELDS)
    ):
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request")
    if request.get("kind") != "Issue":
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request")
    try:
        issue_id = validate_work_object_id(request.get("id"))
        title = request.get("title")
        description = request.get("description", "")
        priority = request.get("priority", 0)
        artifact_ids = _parse_identifier_array(request.get("artifact_ids", []))
        external_link_ids = _parse_identifier_array(
            request.get("external_link_ids", [])
        )
    except (TypeError, ValueError):
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request") from None
    if (
        not isinstance(title, str)
        or not title
        or not isinstance(description, str)
        or type(priority) is not int
        or not DEVGRAPH_JSON_SAFE_INTEGER_MIN_V1
        <= priority
        <= DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
    ):
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request")
    materialized = {
        "artifact_ids": list(artifact_ids),
        "description": description,
        "external_link_ids": list(external_link_ids),
        "id": issue_id,
        "kind": "Issue",
        "priority": priority,
        "title": title,
    }
    try:
        canonical_request = _canonical_json(materialized)
    except SecSIssueCreateDenied:
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request") from None
    if len(canonical_request) > _CANONICAL_REQUEST_MAX_BYTES:
        raise SecSIssueCreateDenied("invalid_devgraph_issue_create_request")
    return (
        Issue(
            id=issue_id,
            title=title,
            description=description,
            priority=priority,
            artifact_ids=artifact_ids,
            external_link_ids=external_link_ids,
        ),
        canonical_request,
    )


def _parse_identifier_array(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("invalid identifier array")
    return tuple(validate_work_object_id(item) for item in value)


def _digest_idempotency_key(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or _IDEMPOTENCY_KEY.fullmatch(
        idempotency_key
    ) is None:
        raise SecSIssueCreateDenied("invalid_devgraph_idempotency_key")
    return hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()


def _strict_json_object(
    raw_json: bytes,
    *,
    maximum_bytes: int,
    reason: str,
) -> dict[str, Any]:
    if type(raw_json) is not bytes or len(raw_json) > maximum_bytes:
        raise SecSIssueCreateDenied(reason)

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
        raise SecSIssueCreateDenied(reason) from None
    if not isinstance(value, dict):
        raise SecSIssueCreateDenied(reason)
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
        raise SecSIssueCreateDenied("invalid_canonical_json") from None


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
        raise SecSIssueCreateDenied(reason)
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise SecSIssueCreateDenied(reason) from None
    if (
        len(decoded) != decoded_length
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        raise SecSIssueCreateDenied(reason)
    return decoded


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: frozenset[str],
    *,
    reason: str,
) -> None:
    if set(value) != fields:
        raise SecSIssueCreateDenied(reason)


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
        if reason.startswith("invalid "):
            raise ValueError(reason)
        raise SecSIssueCreateDenied(reason)
    return value
