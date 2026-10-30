"""Opt-in finite-session admission over the shared native secS contract.

Preparation verifies only the supplied signed parent and request. It never
queries a current assignment or root registry. A host must retain the resulting
challenge before disclosure and verify current parent state after holder proof.
This module alone registers no route and enables no runtime policy.
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import checksum, invoke_native
from devgraph.auth.holder_session_transport import loads
from devgraph.auth.native_read import RESOURCE, _seconds
from devgraph.auth.selector_grants import wrap_permission

READ_OPERATIONS = frozenset(
    {
        "devgraph.work.get", "devgraph.work.list", "devgraph.work.relationships",
        "devgraph.arena.get", "devgraph.arena.list",
    }
)
SAFE = 9007199254740991
OBSERVED_ORIGIN = ContextVar("devgraph_holder_session_origin", default=None)


class HolderSessionPolicy(BaseModel):
    """Separately reviewed receiver policy; never derived from discovery."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_: Literal["devgraph.holder-session-policy.v1"] = Field(
        default="devgraph.holder-session-policy.v1", alias="schema"
    )
    enabled: bool = False
    allowed_origins: list[str] = Field(default_factory=list, max_length=64)
    audience: Literal["devgraph"] = "devgraph"
    max_ttl_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def fixed_context(self):
        if self.enabled and not self.allowed_origins:
            raise ValueError("enabled holder sessions require explicit origins")
        if len(set(self.allowed_origins)) != len(self.allowed_origins) or any(
            not origin
            or len(origin) > 256
            or any(not 33 <= ord(c) <= 126 or c == "*" for c in origin)
            for origin in self.allowed_origins
        ):
            raise ValueError("invalid holder session origins")
        return self


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_: Literal["secs.holder-session-prepare.v1"] = Field(alias="schema")
    request_id: str = Field(pattern="^[0-9a-f]{32}$")
    parent_grant: dict
    installation_id: str = Field(pattern="^[0-9a-f]{32}$")
    session_installation_id: str = Field(pattern="^[0-9a-f]{32}$")
    session_public_key: str = Field(pattern="^[0-9a-f]{64}$")
    origin: str = Field(min_length=1, max_length=256)
    audience: str = Field(min_length=1, max_length=256)
    permissions: list[dict] | None = Field(default=None, max_length=64)
    requested_ttl_seconds: int = Field(ge=1, le=3600)


def _read_permissions(host, records):
    if not isinstance(records, list) or not 1 <= len(records) <= 64:
        raise UnauthenticatedError("holder_session_read_scope_required")
    for permission in records:
        if not isinstance(permission, dict):
            raise ValueError("invalid permission record")
        operation = permission.get("operation")
        if operation not in READ_OPERATIONS or permission.get("resource") != RESOURCE:
            raise UnauthenticatedError("holder_session_read_only")
        decoded = host.provider._decode_grants(
            [{k: v for k, v in permission.items() if k != "roles"}]
        )
        expected_scope = (
            "devgraph.arena.read"
            if operation.startswith("devgraph.arena.")
            else "devgraph.work.read"
        )
        if not decoded or any(g.scope != expected_scope for g in decoded):
            raise UnauthenticatedError("holder_session_read_only")
        wrap_permission(host, permission, decoded)
    return json.loads(canonical_json(records))


def compile_prepare(host, policy: HolderSessionPolicy, raw, *, now=None):
    """Compile an offline challenge without disclosing canonical registry state.

    The caller must retain exact request/challenge bytes transactionally for
    retries; a fresh compiler invocation is not an idempotent exchange outcome.
    This does not assert that a supplied parent is currently registered/active.
    """
    if not policy.enabled:
        raise UnauthenticatedError("holder_sessions_not_enabled")
    try:
        if len(canonical_json(raw)) > 131072:
            raise ValueError("request too large")
        request = PrepareRequest.model_validate(raw)
        current = _seconds(now)
        if request.origin not in policy.allowed_origins or request.audience != policy.audience:
            raise UnauthenticatedError("holder_session_context_denied")
        parent = request.parent_grant
        claims = parent["claims"]
        if claims["schema"] != "secs.holder-grant.v2":
            raise UnauthenticatedError("holder_session_parent_unsupported")
        selected = request.permissions
        if selected is None:
            if not isinstance(claims["permissions"], list) or any(
                not isinstance(p, dict) for p in claims["permissions"]
            ):
                raise ValueError("invalid permission records")
            selected = [p for p in claims["permissions"] if p.get("operation") in READ_OPERATIONS]
        selected = _read_permissions(host, selected)
        cap = claims["max_credential_lifetime_seconds"]
        if type(cap) is not int or not 0 < cap <= SAFE or not 0 <= current <= SAFE:
            raise ValueError("invalid lifetime")
        end = min(
            current + request.requested_ttl_seconds, current + cap, current + policy.max_ttl_seconds
        )
        if claims["validity"]["mode"] == "dated":
            end = min(end, claims["validity"]["not_after"])
        challenge = {
            "schema": "secs.holder-session-challenge.v1",
            "challenge_id": secrets.token_hex(16),
            "request_id": request.request_id,
            "nonce": secrets.token_hex(32),
            **{name: host.pin[name] for name in ("issuer", "key_id", "service")},
            "audience": policy.audience,
            "origin": request.origin,
            "parent_grant_id": claims["grant_id"],
            "parent_grant_digest": checksum(parent),
            "parent_revision": claims["revision"],
            "holder_public_key": claims["holder_public_key"],
            "installation_id": request.installation_id,
            "session_installation_id": request.session_installation_id,
            "session_public_key": request.session_public_key,
            "permissions": selected,
            "issued_at": current,
            "review_deadline": min(current + 120, end),
            "expires_at": end,
        }
        # Preflight the larger exchange envelope before Wallet consent. Dummy
        # signatures are size placeholders only; they are never submitted.
        loads(
            canonical_json(
                {
                    "operation": "issue_holder_session",
                    "authorization": {
                        "schema": "secs.holder-session-authorization.v1",
                        "challenge": challenge,
                        "parent_grant": parent,
                        "holder_signature": "00" * 64,
                        "session_signature": "00" * 64,
                    },
                    "credential_id": "00" * 16,
                    "context": {"origin": request.origin, "audience": policy.audience},
                    "now": current,
                }
            )
        )
        invoke_native(
            host.binary,
            host.binary_sha256,
            host.pin,
            "review_holder_session",
            parent_grant=parent,
            challenge=challenge,
            context={"origin": request.origin, "audience": policy.audience},
            now=current,
        )
        return challenge
    except (KeyError, TypeError, ValueError, OverflowError):
        raise UnauthenticatedError("invalid_holder_session_request") from None


class HolderSessionHost:
    """Canonical retained outcomes; construction requires an explicit policy.

    This is not a router or a general credential verifier. Current-parent checks
    belong inside each exchange/status/read transaction, across all aliases.
    """

    def __init__(self, host, policy: HolderSessionPolicy, *, policy_check=None):
        self.host, self.policy = host, policy
        self.policy_check = policy_check
        self.policy_digest = checksum(policy.model_dump(mode="json", by_alias=True))
        with host.provider._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS holder_sessions (
                    service TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
                    challenge TEXT NOT NULL, authorization_digest TEXT,
                    credential TEXT, PRIMARY KEY(service, request_id));
                CREATE TABLE IF NOT EXISTS holder_session_status_replay (
                    challenge_id TEXT NOT NULL, nonce TEXT NOT NULL,
                    expires INTEGER NOT NULL, PRIMARY KEY(challenge_id, nonce));
                CREATE TABLE IF NOT EXISTS holder_session_expired_reviews (
                    service TEXT NOT NULL, request_id TEXT NOT NULL,
                    challenge_id TEXT NOT NULL,
                    PRIMARY KEY(service, request_id));
                CREATE TRIGGER IF NOT EXISTS holder_session_expired_review_no_issue
                    BEFORE UPDATE OF credential ON holder_sessions
                    WHEN NEW.credential IS NOT NULL AND EXISTS (
                        SELECT 1 FROM holder_session_expired_reviews
                        WHERE service=NEW.service AND request_id=NEW.request_id)
                    BEGIN
                        SELECT RAISE(ABORT, 'holder_session_review_expired');
                    END;
            """)
        host.holder_sessions = self

    @contextmanager
    def from_origin(self, observed_origin):
        """A trusted HTTP/native adapter supplies the observed/profile-bound origin."""
        token = OBSERVED_ORIGIN.set(observed_origin)
        try:
            yield
        finally:
            OBSERVED_ORIGIN.reset(token)

    def _credential_record(self, db, credential, challenge):
        self._context(challenge)
        if OBSERVED_ORIGIN.get() != challenge["origin"]:
            raise UnauthenticatedError("holder_session_observed_origin_required")
        row = self._record(db, challenge["request_id"])
        if row[4] is None or checksum(json.loads(row[4])) != checksum(credential):
            raise UnauthenticatedError("holder_session_credential_changed")

    def verify_request(self, *, credential, call, proof, now=None):
        self._enabled()
        current = _seconds(now)
        try:
            if credential["claims"]["schema"] != "secs.holder-session-credential.v1":
                raise UnauthenticatedError("holder_session_credential_required")
            challenge = credential["claims"]["challenge"]
            context = self._context(challenge)
            _read_permissions(self.host, challenge["permissions"])
            with self.host._provider_context(), self.host.provider._database() as db:
                db.execute("BEGIN")
                self._credential_record(db, credential, challenge)
                self.host.provider._check_policy_snapshot(db)
                parent = self._parent(db, challenge, current)
                verified = invoke_native(
                    self.host.binary,
                    self.host.binary_sha256,
                    self.host.pin,
                    "verify_holder_session",
                    credential=credential,
                    parent_grant=parent,
                    call=call,
                    proof=proof,
                    context=context,
                    now=current,
                )
                db.execute("COMMIT")
            return self.host._verified_context(
                verified, credential, proof, current, v2=True, unbound_parent=True, call=call
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise UnauthenticatedError("invalid_holder_session_presentation") from None

    @contextmanager
    def current_read(self, credential, proof, *, now=None):
        """Keep current-parent and original-token fences through disclosure."""
        challenge = credential["claims"]["challenge"]
        with self.host._current_read_refs(
            challenge["parent_grant_id"],
            challenge["parent_grant_digest"],
            challenge,
            proof,
            now=now,
            extra=lambda db: self._credential_record(db, credential, challenge),
        ):
            yield

    def _enabled(self):
        if not self.policy.enabled:
            raise UnauthenticatedError("holder_sessions_not_enabled")
        if self.policy_check is not None and not self.policy_check():
            raise UnauthenticatedError("holder_session_policy_changed")

    def _context(self, challenge):
        self._enabled()
        if challenge["origin"] not in self.policy.allowed_origins:
            raise UnauthenticatedError("holder_session_context_denied")
        return {"origin": challenge["origin"], "audience": self.policy.audience}

    def _record(self, db, request_id):
        self._enabled()
        row = db.execute(
            "SELECT request_digest,policy_digest,challenge,authorization_digest,credential "
            "FROM holder_sessions WHERE service=? AND request_id=?",
            (self.host.service, request_id),
        ).fetchone()
        if row is None or row[1] != self.policy_digest:
            raise UnauthenticatedError("holder_session_unavailable")
        return row

    def _parent(self, db, challenge, current):
        row = db.execute(
            "SELECT service,owner,device,revision,digest,request_digest,grant,membership,status "
            "FROM holder_roots WHERE id=?",
            (challenge["parent_grant_id"],),
        ).fetchone()
        if (
            row is None
            or row[0] != self.host.service
            or row[1] != challenge["holder_public_key"]
            or row[3] != challenge["parent_revision"]
            or row[4] != challenge["parent_grant_digest"]
        ):
            raise UnauthenticatedError("holder_session_parent_changed")
        self.host._current(row, current)
        return json.loads(row[6])

    @staticmethod
    def _result(challenge, state, credential=None):
        return {
            "schema": "secs.holder-session-result.v1",
            "request_id": challenge["request_id"],
            "challenge_id": challenge["challenge_id"],
            "state": state,
            "credential": credential if state == "issued" else None,
            "expires_at": challenge["expires_at"],
        }

    def _review_expired(self, db, challenge, row, current):
        """Retain terminality for unissued reviews, even after clock rollback.

        This is called only after holder/session authentication while holding
        the canonical write transaction. An issued result is never invalidated
        by the shorter review deadline. Tombstones share retained-row capacity.
        """
        expired = db.execute(
            "SELECT challenge_id FROM holder_session_expired_reviews "
            "WHERE service=? AND request_id=?",
            (self.host.service, challenge["request_id"]),
        ).fetchone()
        if expired is not None:
            if expired[0] != challenge["challenge_id"] or row[4] is not None:
                raise UnauthenticatedError("holder_session_review_changed")
            return True
        if row[4] is not None or current < challenge["review_deadline"]:
            return False
        db.execute(
            "INSERT INTO holder_session_expired_reviews VALUES(?,?,?)",
            (self.host.service, challenge["request_id"], challenge["challenge_id"]),
        )
        return True

    @staticmethod
    def _status_time(challenge, proof, now):
        # Native verification authenticates these exact fields. Recheck their
        # clock limits after crypto/lock waits and immediately before disclosure.
        current = _seconds(now)
        if not (
            challenge["issued_at"] <= current < challenge["expires_at"]
            and proof["issued_at"] <= current < proof["expires_at"]
        ):
            raise UnauthenticatedError("holder_session_status_expired")
        return current

    def prepare(self, body, *, now=None):
        self._enabled()
        # Native signature/subset validation uses only the supplied object. No
        # canonical root/assignment lookup precedes holder proof.
        challenge = compile_prepare(self.host, self.policy, body, now=now)
        self._enabled()
        digest = checksum(body)
        with self.host.provider._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_digest,policy_digest,challenge FROM holder_sessions "
                "WHERE service=? AND request_id=?",
                (self.host.service, challenge["request_id"]),
            ).fetchone()
            if row is not None:
                if row[:2] != (digest, self.policy_digest):
                    raise UnauthenticatedError("holder_session_request_conflict")
                challenge = json.loads(row[2])
            else:
                # Retained outcomes are not silently pruned as a capacity fix.
                if db.execute("SELECT COUNT(*) FROM holder_sessions").fetchone()[0] >= 10000:
                    raise UnauthenticatedError("try_later")
                db.execute(
                    "INSERT INTO holder_sessions VALUES(?,?,?,?,?,NULL,NULL)",
                    (
                        self.host.service,
                        challenge["request_id"],
                        digest,
                        self.policy_digest,
                        canonical_json(challenge).decode(),
                    ),
                )
            db.execute("COMMIT")
        return {"schema": "secs.holder-session-challenge-result.v1", "challenge": challenge}

    def exchange(self, authorization, *, now=None):
        self._enabled()
        try:
            challenge = authorization["challenge"]
            context = self._context(challenge)
            # For an exact already-issued retry, validate the original signatures
            # at the original challenge time. A new issuance still checks the
            # real deadline below; this cannot authorize a late new exchange.
            invoke_native(
                self.host.binary,
                self.host.binary_sha256,
                self.host.pin,
                "verify_holder_session_authorization",
                authorization=authorization,
                context=context,
                now=challenge["issued_at"],
            )
            with self.host._provider_context(), self.host.provider._database() as db:
                db.execute("BEGIN IMMEDIATE")
                # Admission time is sampled after acquiring the canonical lock.
                # A request queued before deadline cannot issue using stale time.
                current = _seconds(now)
                if current < challenge["issued_at"]:
                    raise UnauthenticatedError("holder_session_not_yet_valid")
                self.host.provider._check_policy_snapshot(db)
                row = self._record(db, challenge["request_id"])
                if canonical_json(json.loads(row[2])) != canonical_json(challenge):
                    raise UnauthenticatedError("holder_session_review_changed")
                digest = checksum(authorization)
                if row[3] is not None and row[3] != digest:
                    raise UnauthenticatedError("holder_session_exchange_conflict")
                if current >= challenge["expires_at"]:
                    self._review_expired(db, challenge, row, current)
                    db.execute("COMMIT")
                    return self._result(challenge, "expired")
                parent = self._parent(db, challenge, current)
                if checksum(parent) != checksum(authorization["parent_grant"]):
                    raise UnauthenticatedError("holder_session_parent_changed")
                if row[4] is not None:
                    current = _seconds(now)
                    if current < challenge["issued_at"]:
                        raise UnauthenticatedError("holder_session_not_yet_valid")
                    if current >= challenge["expires_at"]:
                        db.execute("COMMIT")
                        return self._result(challenge, "expired")
                    self._parent(db, challenge, current)
                    db.execute("COMMIT")
                    return self._result(challenge, "issued", json.loads(row[4]))
                if self._review_expired(db, challenge, row, current):
                    db.execute("COMMIT")
                    return self._result(challenge, "expired")
                credential = self.host.invoke(
                    "issue_holder_session",
                    authorization=authorization,
                    credential_id=secrets.token_hex(16),
                    context=context,
                    now=current,
                )
                self._enabled()
                current = _seconds(now)
                if current < challenge["issued_at"]:
                    raise UnauthenticatedError("holder_session_not_yet_valid")
                if self._review_expired(db, challenge, row, current):
                    # The token generated during crypto is not a retained issued
                    # result and must never be disclosed after admission expires.
                    db.execute("COMMIT")
                    return self._result(challenge, "expired")
                self._parent(db, challenge, current)
                self._enabled()
                current = _seconds(now)
                if current < challenge["issued_at"]:
                    raise UnauthenticatedError("holder_session_not_yet_valid")
                if self._review_expired(db, challenge, row, current):
                    db.execute("COMMIT")
                    return self._result(challenge, "expired")
                db.execute(
                    "UPDATE holder_sessions SET authorization_digest=?,credential=? "
                    "WHERE service=? AND request_id=? AND credential IS NULL",
                    (
                        digest,
                        canonical_json(credential).decode(),
                        self.host.service,
                        challenge["request_id"],
                    ),
                )
                db.execute("COMMIT")
                return self._result(challenge, "issued", credential)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise UnauthenticatedError("invalid_holder_session_exchange") from None

    def status(self, proof, *, now=None, expected_context=None):
        self._enabled()
        try:
            with self.host._provider_context(), self.host.provider._database() as db:
                db.execute("BEGIN IMMEDIATE")
                current = _seconds(now)
                row = self._record(db, proof["request_id"])
                challenge = json.loads(row[2])
                context = self._context(challenge)
                if expected_context is not None and expected_context != context:
                    raise UnauthenticatedError("holder_session_context_denied")
                invoke_native(
                    self.host.binary,
                    self.host.binary_sha256,
                    self.host.pin,
                    "verify_holder_session_status",
                    challenge=challenge,
                    proof=proof,
                    context=context,
                    now=current,
                )
                current = self._status_time(challenge, proof, now)
                # Only a valid temporary-key proof may learn original outcome or
                # current-parent state. Status proofs have a separate replay set.
                db.execute("DELETE FROM holder_session_status_replay WHERE expires<=?", (current,))
                if (
                    db.execute("SELECT COUNT(*) FROM holder_session_status_replay").fetchone()[0]
                    >= 10000
                    or db.execute(
                        "SELECT 1 FROM holder_session_status_replay "
                        "WHERE challenge_id=? AND nonce=?",
                        (challenge["challenge_id"], proof["nonce"]),
                    ).fetchone()
                ):
                    raise UnauthenticatedError("holder_session_status_replay_or_capacity")
                self.host.provider._check_policy_snapshot(db)
                try:
                    self._parent(db, challenge, current)
                except UnauthenticatedError:
                    state = "revoked"
                else:
                    current = self._status_time(challenge, proof, now)
                    if self._review_expired(db, challenge, row, current):
                        state = "expired"
                    else:
                        state = "issued" if row[4] is not None else "pending"
                db.execute(
                    "INSERT INTO holder_session_status_replay VALUES(?,?,?)",
                    (challenge["challenge_id"], proof["nonce"], proof["expires_at"]),
                )
                result = self._result(
                    challenge, state, json.loads(row[4]) if row[4] is not None else None
                )
                self._enabled()
                self._status_time(challenge, proof, now)
                db.execute("COMMIT")
                return result
        except (KeyError, TypeError, ValueError, OverflowError):
            raise UnauthenticatedError("invalid_holder_session_status") from None
