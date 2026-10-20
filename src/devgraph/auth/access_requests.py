"""Opt-in authenticated request inbox; never grants or issues authority.

These new application request signatures do not alter any existing Wallet,
secS credential, Work, or enrollment signing bytes. Wallet support is separate.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import stat
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from devgraph.auth.access_policy import WriteGrant
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import AccessDraftV2, EnrollmentPolicyV2, MonitorWorkGrant
from devgraph.auth.native_read import _seconds

DOMAIN = b"devgraph/access-request-approval/v1\0"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AccessIntent(Closed):
    """Untrusted requested permissions, not an assignment or an authority ceiling."""

    read_grants: tuple[MonitorWorkGrant, ...] = Field(default=(), max_length=16)
    write_grants: tuple[WriteGrant, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.read_grants and not self.write_grants:
            raise ValueError("omit intent to request unspecified access")
        for grants in (self.read_grants, self.write_grants):
            if len({canonical_json(g) for g in grants}) != len(grants):
                raise ValueError("duplicate grant")
        return self


class RequestAction(Closed):
    schema_: Literal["devgraph.access-request-action.v1"] = Field(alias="schema")
    action: Literal["submit", "status"]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    subject_public_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    intent: AccessIntent | None

    @model_validator(mode="after")
    def status_has_no_selection(self):
        if self.action == "status" and self.intent is not None:
            raise ValueError("status never proposes or changes permissions")
        return self


class Challenge(Closed):
    schema_: Literal["devgraph.access-request-challenge.v1"] = Field(alias="schema")
    challenge_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    issuer: str
    receiver: str
    issued_at: int = Field(strict=True, ge=0)
    expires_at: int = Field(strict=True, ge=1)
    request: RequestAction


def signable_access_request(challenge: dict) -> bytes:
    """Sign the complete disclosed challenge with its new application domain."""
    return DOMAIN + canonical_json(challenge)


class AccessRequestInbox:
    challenge_type = Challenge

    @staticmethod
    def parse_request(value):
        return RequestAction.model_validate(value)

    @staticmethod
    def challenge_schema(request):
        return "devgraph.access-request-challenge.v1"

    @staticmethod
    def decode_assignment(raw):
        return AccessDraftV2.model_validate_json(raw)

    def validate_intent(self, intent, current):
        if intent:
            AccessDraftV2.model_validate(dict(
                schema="devgraph.access-draft.v2", issuer=self.issuer, receiver=self.receiver,
                write_audience="devgraph://receiver-local", not_before=current,
                not_after=current + 604800, lifetime_seconds=60,
                read_grants=intent.read_grants, write_grants=intent.write_grants,
            ))

    def __init__(
        self,
        *,
        path: Path,
        issuer: str,
        receiver: str,
        membership_check: Callable[[dict, str], bool],
        max_requests: int = 10000,
        max_per_member: int = 32,
    ):
        # Reuse canonical issuer/origin validation; do not invent a default server.
        dummy = MonitorWorkGrant(
            scope="devgraph.work.read",
            operations=("get",),
            relationship_types=("parent",),
            work_kinds=("Task",),
            include_archived=False,
        )
        EnrollmentPolicyV2.model_validate(
            dict(
                schema="devgraph.native-read-policy.v2",
                issuer=issuer,
                receiver=receiver,
                grants=(dummy,),
                lifetime_seconds=60,
            )
        )
        if (
            type(max_requests) is not int
            or not 1 <= max_requests <= 10000
            or type(max_per_member) is not int
            or not 1 <= max_per_member <= 32
        ):
            raise ValueError("invalid inbox capacity")
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("inbox requires an existing owner-private directory")
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o077
        ):
            raise ValueError("inbox directory must be owner-private")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe inbox database")
        self.path, self.issuer, self.receiver = path, issuer, receiver
        self.membership_check = membership_check
        self.max_requests, self.max_per_member = max_requests, max_per_member
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inbox_binding (
                    id INTEGER PRIMARY KEY CHECK(id=1), issuer TEXT, receiver TEXT);
                CREATE TABLE IF NOT EXISTS access_challenges (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL,
                    expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS access_requests (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, device TEXT NOT NULL,
                    digest TEXT NOT NULL, intent TEXT NOT NULL,
                    created INTEGER NOT NULL, expires INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS request_owner ON access_requests(owner);
                CREATE TABLE IF NOT EXISTS access_request_audit (
                    request_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    owner TEXT NOT NULL, device TEXT NOT NULL, created INTEGER NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT issuer,receiver FROM inbox_binding WHERE id=1").fetchone()
            if row and row != (issuer, receiver):
                raise ValueError("inbox binding changed")
            db.execute("INSERT OR IGNORE INTO inbox_binding VALUES(1,?,?)", (issuer, receiver))
            db.execute("COMMIT")

    @contextmanager
    def _database(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            yield db
        finally:
            db.close()

    def challenge(self, request: RequestAction, *, now: datetime | None = None) -> dict:
        current = _seconds(now)
        if not isinstance(request, RequestAction):
            raise UnauthenticatedError("invalid_request")
        self.validate_intent(request.intent, current)
        value = dict(
            schema=self.challenge_schema(request),
            challenge_id=secrets.token_hex(16),
            nonce=secrets.token_urlsafe(32),
            issuer=self.issuer,
            receiver=self.receiver,
            issued_at=current,
            expires_at=current + 120,
            request=request.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        )
        payload = canonical_json(value)
        if len(payload) > 16384:
            raise UnauthenticatedError("invalid_request")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM access_challenges WHERE expires<=?", (current,))
            if (
                db.execute("SELECT count(*) FROM access_challenges").fetchone()[0] >= 256
                or db.execute(
                    "SELECT count(*) FROM access_challenges WHERE owner=?",
                    (request.subject_public_key,),
                ).fetchone()[0]
                >= 8
            ):
                raise UnauthenticatedError("try_later")
            db.execute(
                "INSERT INTO access_challenges VALUES(?,?,?,?,0)",
                (
                    value["challenge_id"],
                    request.subject_public_key,
                    payload.decode(),
                    value["expires_at"],
                ),
            )
            db.execute("COMMIT")
        return value

    def execute(
        self, challenge: dict, proof: dict, signature: str, *, now: datetime | None = None
    ) -> dict:
        current = _seconds(now)
        try:
            parsed = self.challenge_type.model_validate(challenge)
            if (
                len(canonical_json(challenge)) > 16384
                or not isinstance(proof, dict)
                or len(canonical_json(proof)) > 16384
                or parsed.issuer != self.issuer
                or parsed.receiver != self.receiver
                or not parsed.issued_at <= current < parsed.expires_at
                or parsed.expires_at - parsed.issued_at != 120
                or not isinstance(signature, str)
                or len(signature) != 86
            ):
                raise ValueError()
            raw = base64.urlsafe_b64decode(signature + "==")
            if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != signature:
                raise ValueError()
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(parsed.request.subject_public_key)
            ).verify(raw, signable_access_request(challenge))
        except (ValueError, TypeError, InvalidSignature):
            raise UnauthenticatedError("invalid_request_approval") from None
        return self._record_authenticated(challenge, parsed, proof, current)

    def _record_authenticated(self, challenge, parsed, proof, current):
        request = parsed.request
        # Hash explicit request JSON, not the fresh nonce: exact request retries
        # reconcile after a lost response, without creating another request.
        digest = hashlib.sha256(canonical_json(challenge["request"])).hexdigest()
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload,used FROM access_challenges WHERE id=?", (parsed.challenge_id,)
            ).fetchone()
            if not row or row[0].encode() != canonical_json(challenge):
                raise UnauthenticatedError("invalid_challenge")
            # Recheck approved membership/revocation for every action, including
            # exact duplicate/status recovery, while holding the mutation lock.
            if not self.membership_check(proof, request.subject_public_key):
                raise UnauthenticatedError("membership_required")
            record = db.execute(
                "SELECT * FROM access_requests WHERE id=?", (request.request_id,)
            ).fetchone()
            if record and record[1:3] != (request.subject_public_key, request.installation_id):
                raise UnauthenticatedError("request_unavailable")
            if request.action == "status":
                if not record:
                    # Missing record is explicitly unknown; never resubmit.
                    result = dict(
                        schema="devgraph.access-request-result.v1",
                        request_id=request.request_id,
                        status="unknown",
                        authority_issued=False,
                    )
                else:
                    result = self._result(record, current)
            else:
                if record and record[3] != digest:
                    raise UnauthenticatedError("request_conflict")
                if not record:
                    self.validate_intent(request.intent, current)
                    if row[1]:
                        raise UnauthenticatedError("challenge_used")
                    if (
                        db.execute("SELECT count(*) FROM access_requests").fetchone()[0]
                        >= self.max_requests
                        or db.execute(
                            "SELECT count(*) FROM access_requests WHERE owner=? AND expires>?",
                            (request.subject_public_key, current),
                        ).fetchone()[0]
                        >= self.max_per_member
                    ):
                        raise UnauthenticatedError("try_later")
                    intent = (
                        canonical_json(challenge["request"]["intent"]).decode()
                        if request.intent
                        else "null"
                    )
                    record = (
                        request.request_id,
                        request.subject_public_key,
                        request.installation_id,
                        digest,
                        intent,
                        current,
                        current + 604800,
                    )
                    db.execute("INSERT INTO access_requests VALUES(?,?,?,?,?,?,?)", record)
                    db.execute(
                        "INSERT INTO access_request_audit VALUES(?,?,?,?,?)",
                        (
                            request.request_id,
                            digest,
                            request.subject_public_key,
                            request.installation_id,
                            current,
                        ),
                    )
                result = self._result(record, current)
            db.execute("UPDATE access_challenges SET used=1 WHERE id=?", (parsed.challenge_id,))
            db.execute("COMMIT")
        return result

    @staticmethod
    def _result(record, current):
        return dict(
            schema="devgraph.access-request-result.v1",
            request_id=record[0],
            request_digest_sha256=record[3],
            status="pending" if current < record[6] else "expired",
            received_at=record[5],
            expires_at=record[6],
            authority_issued=False,
        )

    def pending(
        self, *, limit: int = 50, after_id: str | None = None, now: datetime | None = None
    ) -> dict:
        """Owner-process projection only; no public operator HTTP route.

        A future operator route must independently authorize the administrator.
        Possession of an applicant request or membership never permits listing.
        """
        import json
        import re

        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (
                after_id is not None
                and (not isinstance(after_id, str) or not re.fullmatch(r"[0-9a-f]{32}", after_id))
            )
        ):
            raise ValueError("invalid inbox page")
        current = _seconds(now)
        with self._database() as db:
            db.execute("BEGIN")
            count = db.execute(
                "SELECT count(*) FROM access_requests WHERE expires>?", (current,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM access_requests WHERE expires>? AND id>? ORDER BY id LIMIT ?",
                (current, after_id or "", limit + 1),
            ).fetchall()
            db.execute("COMMIT")
        return dict(
            schema="devgraph.access-request-inbox.v1",
            total=count,
            next_cursor=rows[limit - 1][0] if len(rows) > limit else None,
            items=[
                dict(
                    self._result(row, current),
                    subject_public_key=row[1],
                    installation_id=row[2],
                    request_digest_sha256=row[3],
                    intent=json.loads(row[4]),
                )
                for row in rows[:limit]
            ],
        )

    def wallet_challenge(self, request: RequestAction, *, now=None):
        legacy = self.challenge(request, now=now)
        return dict(
            schema="castalia.access-request-challenge.v1",
            challenge_id=legacy["challenge_id"],
            nonce=legacy["nonce"],
            issuer=self.issuer,
            receiver=self.receiver,
            subject_public_key=request.subject_public_key,
            installation_id=request.installation_id,
            operation="access.request" if request.action == "submit" else "access.status",
            issued_at=legacy["issued_at"],
            expires_at=legacy["expires_at"],
            details=legacy["request"],
        )

    def execute_wallet(self, challenge, proof, signature, *, now=None):
        from devgraph.auth.access_approval import verify_wallet_approval

        current = _seconds(now)
        parsed = verify_wallet_approval(
            challenge, signature, issuer=self.issuer, receiver=self.receiver, current=current
        )
        request = self.parse_request(parsed.details)
        if (
            parsed.subject_public_key != request.subject_public_key
            or parsed.installation_id != request.installation_id
            or parsed.operation
            != ("access.request" if request.action == "submit" else "access.status")
            or not isinstance(proof, dict)
            or len(canonical_json(proof)) > 16384
        ):
            raise UnauthenticatedError("invalid_access_approval")
        legacy = dict(
            schema=self.challenge_schema(request),
            challenge_id=parsed.challenge_id,
            nonce=parsed.nonce,
            issuer=self.issuer,
            receiver=self.receiver,
            issued_at=parsed.issued_at,
            expires_at=parsed.expires_at,
            request=parsed.details,
        )
        result = self._record_authenticated(
            legacy, self.challenge_type.model_validate(legacy), proof, current
        )
        if getattr(self, "provisioning", None) is not None:
            self.provisioning.refresh()
        with self._database() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='access_decisions'").fetchone():
                decision = db.execute(
                    "SELECT version,status FROM access_decisions WHERE request_id=?",
                    (request.request_id,),
                ).fetchone()
                if decision:
                    result.update(
                        schema="devgraph.access-request-result.v2",
                        version=decision[0],
                        status=decision[1],
                    )
                    if getattr(self, "provisioning", None) is not None and decision[1] in (
                        "active",
                        "revoked",
                    ):
                        result["schema"] = "devgraph.access-request-result.v3"
                        assignment = db.execute(
                            "SELECT request_id,assignment,active FROM access_assignments "
                            "WHERE owner=? AND device=?",
                            (request.subject_public_key, request.installation_id),
                        ).fetchone()
                        state = "revoked"
                        if assignment and assignment[0] != request.request_id:
                            state = "superseded"
                        elif assignment and assignment[2] and decision[1] == "active":
                            grant = self.decode_assignment(assignment[1])
                            state = (
                                "active"
                                if grant.not_before <= current < grant.not_after
                                else "expired"
                            )
                        result.update(status=state, grant_active=state == "active")
                        if hasattr(self.provisioning, "request_result"):
                            write_result = self.provisioning.request_result(
                                db, request.subject_public_key, request.request_id, current
                            )
                            if write_result is not None:
                                result.update(write_result)
        return result
