"""Opt-in native read enrollment. No Work mutation authority or live defaults.

The receiver owns policy and membership validation. A public membership proof is
eligibility, never a grant. Native credentials are digest-registered, per wallet
and installation; browser v1 manifests/signing bytes remain unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.delegated_contract import WorkReadGrant, _grant_is_subset, canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.storage.delegated import validate_selectors

DOMAIN = b"castalia/read-access-approval/v1\0"
HEX = re.compile(r"^[0-9a-f]{64}$")
DEVICE = re.compile(r"^[0-9a-f]{32}$")
TOKEN = re.compile(r"^dgrn1_[A-Za-z0-9_-]{43}$")
RESOURCE = "https://work.zenith-research.ca/devgraph"


class EnrollmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_: Literal["devgraph.native-read-policy.v1"] = Field(alias="schema")
    issuer: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
    receiver: str
    grants: tuple[WorkReadGrant, ...] = Field(min_length=1, max_length=16)
    lifetime_seconds: int = Field(ge=60, le=86400, strict=True)

    @model_validator(mode="after")
    def validate_policy(self):
        url = urlsplit(self.receiver)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("receiver must be an HTTPS origin without a trailing slash")
        if any(set(g.operations) - {"list", "get"} for g in self.grants):
            raise ValueError("native v1 admits only bounded Work list/get")
        for grant in self.grants:
            validate_selectors(grant.work_ids, grant.arena_ids)
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self)).hexdigest()


def signable_challenge(challenge: dict) -> bytes:
    return DOMAIN + canonical_json(challenge)


def _seconds(now: datetime | None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise UnauthenticatedError("invalid_time")
    return int(current.timestamp())


class NativeReadEnrollment:
    def __init__(
        self,
        *,
        path: Path,
        policy: Callable[[], EnrollmentPolicy],
        membership_check: Callable[[dict, str], bool],
    ):
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("native enrollment needs an existing private storage directory")
        parent_info = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) & 0o077
        ):
            raise ValueError("native enrollment directory must be owner-private")
        self.path, self.policy, self.membership_check = path, policy, membership_check
        # Explicit host composition owns parent-directory provisioning. Never use
        # a user's default credential directory or auto-load an owner read key.
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
            raise ValueError("unsafe native enrollment database")
        self.policy()  # Invalid/missing explicit policy fails setup, not open access.
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS challenges (id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS enrollments (owner TEXT, device TEXT,
                    ceiling TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,device));
                CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY,
                    digest TEXT UNIQUE NOT NULL,
                    owner TEXT NOT NULL, device TEXT NOT NULL, manifest TEXT NOT NULL,
                    proof TEXT NOT NULL, status TEXT NOT NULL);
            """)

    @contextmanager
    def _database(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            yield db
        finally:
            db.close()

    def _check_policy_snapshot(self, db) -> None:
        """Opt-in policy adapters may reject a concurrently superseded snapshot."""

    def _policy_for(self, owner: str, device: str, current: int) -> EnrollmentPolicy:
        return self.policy()

    def _credential_expiry(self, policy: EnrollmentPolicy, owner, device, current) -> int:
        return current + policy.lifetime_seconds

    def challenge(self, owner: str, device: str, *, now: datetime | None = None) -> dict:
        if (
            not isinstance(owner, str)
            or not HEX.fullmatch(owner)
            or not isinstance(device, str)
            or not DEVICE.fullmatch(device)
        ):
            raise UnauthenticatedError("invalid_request")
        current = _seconds(now)
        policy = self._policy_for(owner, device, current)
        value = {
            "schema": "castalia.read-access-challenge.v1",
            "request_id": secrets.token_hex(16),
            "nonce": secrets.token_urlsafe(32),
            "subject_public_key": owner,
            "installation_id": device,
            "issuer": policy.issuer,
            "receiver": policy.receiver,
            "details": self._details(policy),
            "issued_at": current,
            "expires_at": current + 120,
            "credential_expires_at": self._credential_expiry(policy, owner, device, current),
        }
        if len(canonical_json(value)) > 16384:
            raise UnauthenticatedError("read_policy_too_large")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_policy_snapshot(db)
                db.execute("DELETE FROM challenges WHERE expires < ?", (current,))
                if db.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] >= 256:
                    raise UnauthenticatedError("try_later")
                db.execute(
                    "INSERT INTO challenges(id,payload,expires) VALUES(?,?,?)",
                    (value["request_id"], canonical_json(value).decode(), value["expires_at"]),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return value

    def _decode_enrollment_ceiling(self, grants):
        # Persisted scopes use the contract selected by the enrollment service.
        # The legacy service keeps its closed five-kind WorkReadGrant decoder.
        return tuple(WorkReadGrant.model_validate(grant) for grant in grants)

    def enroll(
        self, challenge: dict, proof: dict, signature: str, *, now: datetime | None = None
    ) -> dict:
        current = _seconds(now)
        if (
            not isinstance(challenge, dict)
            or not isinstance(challenge.get("details"), dict)
            or not isinstance(proof, dict)
            or len(canonical_json(proof)) > 16384
        ):
            raise UnauthenticatedError("invalid_request")
        owner, device = challenge.get("subject_public_key"), challenge.get("installation_id")
        if (
            not isinstance(owner, str)
            or not HEX.fullmatch(owner)
            or not isinstance(device, str)
            or not DEVICE.fullmatch(device)
        ):
            raise UnauthenticatedError("invalid_request")
        policy = self._policy_for(owner, device, current)
        if (
            challenge.get("details", {}).get("policy_digest") != policy.digest
            or challenge.get("receiver") != policy.receiver
        ):
            raise UnauthenticatedError("policy_changed")
        if not self.membership_check(proof, owner):
            raise UnauthenticatedError("membership_required")
        try:
            if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
                raise ValueError()
            sig = base64.urlsafe_b64decode(signature + "==")
            if base64.urlsafe_b64encode(sig).rstrip(b"=").decode() != signature:
                raise ValueError()
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(owner)).verify(
                sig, signable_challenge(challenge)
            )
        except (ValueError, InvalidSignature):
            raise UnauthenticatedError("invalid_possession") from None
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._check_policy_snapshot(db)
                row = db.execute(
                    "SELECT payload,expires,used FROM challenges WHERE id=?",
                    (challenge.get("request_id"),),
                ).fetchone()
                if (
                    not row
                    or row[0] != canonical_json(challenge).decode()
                    or current >= row[1]
                    or current < challenge["issued_at"]
                ):
                    raise UnauthenticatedError("challenge_expired")
                if row[2]:
                    raise UnauthenticatedError("challenge_used")
                enrollment = db.execute(
                    "SELECT ceiling,revoked FROM enrollments WHERE owner=? AND device=?",
                    (owner, device),
                ).fetchone()
                if enrollment:
                    if enrollment[1]:
                        raise UnauthenticatedError("credential_revoked")
                    ceiling = self._decode_enrollment_ceiling(json.loads(enrollment[0]))
                    if any(
                        not any(_grant_is_subset(g, allowed) for allowed in ceiling)
                        for g in policy.grants
                    ):
                        raise UnauthenticatedError("grant_amplification")
                else:
                    if db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] >= 10000:
                        raise UnauthenticatedError("try_later")
                    db.execute(
                        "INSERT INTO enrollments VALUES(?,?,?,0)",
                        (
                            owner,
                            device,
                            canonical_json(challenge["details"]["read_grants"]).decode(),
                        ),
                    )
                identifier = "dgrn_" + secrets.token_hex(16)
                manifest = {
                    "schema": "devgraph.native-read-manifest.v1",
                    "credential_id": identifier,
                    "subject_public_key": owner,
                    "installation_id": device,
                    "issuer": policy.issuer,
                    "receiver": policy.receiver,
                    "policy_digest": policy.digest,
                    "audience": "devgraph",
                    "resource": RESOURCE,
                    "issued_at": current,
                    "expires_at": challenge["credential_expires_at"],
                    "read_grants": challenge["details"]["read_grants"],
                }
                credential = self._mint(manifest, current)
                identifier = manifest["credential_id"]
                db.execute(
                    "UPDATE credentials SET status='superseded' "
                    "WHERE owner=? AND device=? AND status='active'",
                    (owner, device),
                )
                db.execute(
                    "INSERT INTO credentials VALUES(?,?,?,?,?,?,?)",
                    (
                        identifier,
                        hashlib.sha256(credential.encode()).hexdigest(),
                        owner,
                        device,
                        canonical_json(manifest).decode(),
                        canonical_json(proof).decode(),
                        "active",
                    ),
                )
                db.execute("UPDATE challenges SET used=1 WHERE id=?", (challenge["request_id"],))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return self._result(credential, manifest)

    def _details(self, policy: EnrollmentPolicy) -> dict:
        return {
            "schema": "devgraph.native-read-scope.v1",
            "policy_digest": policy.digest,
            "read_grants": [
                g.model_dump(mode="json", exclude_defaults=True) for g in policy.grants
            ],
        }

    def _mint(self, manifest: dict, current: int) -> str:
        return "dgrn1_" + secrets.token_urlsafe(32)

    def _result(self, credential: str, manifest: dict) -> dict:
        return {
            "schema": "devgraph.native-read-result.v1",
            "credential": credential,
            "manifest": manifest,
        }

    def verify(
        self, credential: str | None, *, audience: str, now: datetime | None = None
    ) -> AuthorityContext:
        if (
            audience != "devgraph"
            or not isinstance(credential, str)
            or not TOKEN.fullmatch(credential)
        ):
            raise UnauthenticatedError("invalid_credential")
        current, policy = _seconds(now), self.policy()
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with self._database() as db:
            row = db.execute(
                "SELECT manifest,proof,status FROM credentials WHERE digest=?", (digest,)
            ).fetchone()
        if row is None or row[2] != "active":
            raise UnauthenticatedError("invalid_credential")
        manifest, proof = json.loads(row[0]), json.loads(row[1])
        if not hmac.compare_digest(
            manifest["policy_digest"], policy.digest
        ) or not self.membership_check(proof, manifest["subject_public_key"]):
            raise UnauthenticatedError("policy_changed")
        if current < manifest["issued_at"] or current >= manifest["expires_at"]:
            raise UnauthenticatedError("credential_expired")
        grants = tuple(WorkReadGrant.model_validate(g) for g in manifest["read_grants"])
        return AuthorityContext(
            envelope=CredentialEnvelope(
                actor_id=manifest["credential_id"],
                session_id=manifest["credential_id"],
                correlation_id=manifest["credential_id"],
                scopes=frozenset({"devgraph.work.read"}),
                expires_at=datetime.fromtimestamp(manifest["expires_at"], timezone.utc),
                issuer=policy.issuer,
                audience="devgraph",
                credential_id=manifest["credential_id"],
                credential_version=1,
                read_grants=grants,
                lifecycle_status="active",
                resource=RESOURCE,
            )
        )

    def revoke(self, credential_id: str) -> None:
        """Explicit operator operation; no public HTTP revocation or grant admin route."""
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT owner,device FROM credentials WHERE id=?", (credential_id,)
                ).fetchone()
                if row:
                    db.execute("UPDATE enrollments SET revoked=1 WHERE owner=? AND device=?", row)
                    db.execute(
                        "UPDATE credentials SET status='revoked' WHERE owner=? AND device=?", row
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
