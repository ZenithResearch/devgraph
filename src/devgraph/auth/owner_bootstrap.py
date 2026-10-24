"""Internal first-owner bootstrap. This module exposes no network routes.

Arming is an installation-owner action over a protected local channel. Merely
being a local HTTP client is never sufficient authorization to arm or redeem.
The retained grant must be integrated with the operator verifier before any
claim that it enables installed administration can be made.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

OWNER_DOMAIN = b"zenith/resource-owner-approval/v1\0"
OPERATIONS = ("access.inbox", "access.review", "access.decide", "access.status")
CHALLENGE_SECONDS = 120
SETUP_MAX_SECONDS = 900


class BootstrapError(ValueError):
    """A closed bootstrap boundary. Messages never contain supplied secrets."""


def canonical(value):
    # The v1 signed shape contains only objects, strings, bools and integer
    # timestamps. With these ASCII keys/values this is also RFC 8785/JCS.
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise BootstrapError("invalid_hex")
    return value


def _time(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise BootstrapError("invalid_time")
    return value


@dataclass(frozen=True)
class BootstrapBinding:
    resource_id: str
    endpoint: str
    configuration_sha256: str
    registration_sha256: str

    def __post_init__(self):
        try:
            if str(UUID(self.resource_id)) != self.resource_id:
                raise ValueError()
            parsed = urlsplit(self.endpoint)
            if (
                len(self.endpoint) > 2048
                or not self.endpoint.isascii()
                or any(
                    char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.endpoint
                )
                or "\\" in self.endpoint
                or "%" in self.endpoint
                or parsed.netloc != parsed.netloc.lower()
                or parsed.port == {"http": 80, "https": 443}.get(parsed.scheme)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path
                or parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.port == 0
                or self.endpoint != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError()
            # A literal loopback origin is allowed only for this internal
            # OS-authorized setup binding, never as bearer HTTP authority.
            if parsed.scheme == "http" and not ipaddress.ip_address(parsed.hostname).is_loopback:
                raise ValueError()
            _hex(self.configuration_sha256, 64)
            _hex(self.registration_sha256, 64)
        except (ValueError, TypeError, AttributeError) as exc:
            raise BootstrapError("invalid_binding") from exc


class BootstrapStore:
    def __init__(self, path, binding, *, current_binding):
        if not isinstance(binding, BootstrapBinding):
            raise BootstrapError("invalid_binding")
        self.path = Path(path)
        self.binding = binding
        if not callable(current_binding):
            raise BootstrapError("current_binding_required")
        self.current_binding = current_binding
        self.before_commit = None
        # The caller provides an already-created owner-private directory. Do
        # not create or chmod a broader parent or follow a symlink into one.
        if not self.path.is_absolute() or self.path.parent.resolve() != self.path.parent:
            raise BootstrapError("unsafe_store_path")
        self._private(self.path.parent, directory=True)
        try:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._private(self.path)
        with self._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS bootstrap "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS admin_grants "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS bootstrap_events "
                "(sequence INTEGER PRIMARY KEY, document TEXT NOT NULL)"
            )
            row = db.execute("SELECT document FROM bootstrap WHERE singleton=1").fetchone()
            if row is None:
                self._save(
                    db,
                    {
                        "schema": "zenith.owner-bootstrap-state.v1",
                        "binding": asdict(binding),
                        "state": "uninitialized",
                    },
                )
            else:
                self._load(db)

    @staticmethod
    def _private(path, *, directory=False):
        info = path.lstat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)
        ):
            raise BootstrapError("unsafe_store_permissions")

    @contextmanager
    def _transaction(self):
        self._check_binding()
        self._private(self.path)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            self._check_binding()
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _check_binding(self):
        # The adapter must re-read the OS-owned selected resource/configuration,
        # not a value from the claim. Recheck inside the transaction immediately
        # before commit as well, so detected drift rolls back both grant + burn.
        current = self.current_binding()
        if not isinstance(current, BootstrapBinding) or current != self.binding:
            raise BootstrapError("binding_changed")

    def _load(self, db):
        row = db.execute("SELECT document FROM bootstrap WHERE singleton=1").fetchone()
        if row is None:
            raise BootstrapError("state_missing")
        state = json.loads(row[0])
        if (
            state.get("schema") != "zenith.owner-bootstrap-state.v1"
            or state.get("binding") != asdict(self.binding)
            or state.get("state") not in ("uninitialized", "armed", "prepared", "active")
        ):
            raise BootstrapError("binding_or_state_changed")
        return state

    @staticmethod
    def _save(db, state):
        db.execute(
            "INSERT OR REPLACE INTO bootstrap VALUES (1, ?)",
            (canonical(state).decode(),),
        )

    def _secret_digest(self, secret):
        _hex(secret, 64)
        return hashlib.sha256(
            b"zenith/owner-setup-secret/v1\0"
            + canonical(asdict(self.binding))
            + bytes.fromhex(secret)
        ).hexdigest()

    def _authorize(self, state, secret, now):
        digest = self._secret_digest(secret)
        if not hmac.compare_digest(state.get("setup_digest", ""), digest):
            raise BootstrapError("setup_not_authorized")
        if now < state["armed_at"] or now >= state["setup_expires_at"]:
            raise BootstrapError("setup_expired")

    def arm(self, secret, *, now, expires_at):
        _time(now)
        _time(expires_at)
        if not now < expires_at <= now + SETUP_MAX_SECONDS:
            raise BootstrapError("invalid_setup_deadline")
        digest = self._secret_digest(secret)
        with self._transaction() as db:
            state = self._load(db)
            if state["state"] == "active":
                raise BootstrapError("already_initialized")
            if state["state"] != "uninitialized":
                raise BootstrapError("setup_already_armed")
            state.update(
                state="armed",
                setup_digest=digest,
                armed_at=now,
                setup_expires_at=expires_at,
            )
            self._save(db, state)

    def prepare_claim(self, secret, public_key, claim_id, *, now, grant_id=None):
        _time(now)
        _hex(public_key, 64)
        _hex(claim_id, 32)
        if grant_id is not None:
            _hex(grant_id, 64)
        with self._transaction() as db:
            state = self._load(db)
            if state["state"] == "active":
                raise BootstrapError("already_initialized")
            self._authorize(state, secret, now)
            if state["state"] == "prepared":
                challenge = state["challenge"]
                if (
                    challenge["claim_id"] != claim_id
                    or challenge["admin_grant"]["holder_public_key"] != public_key
                ):
                    raise BootstrapError("claim_pending")
                if grant_id is not None and challenge["admin_grant"]["grant_id"] != grant_id:
                    raise BootstrapError("claim_changed")
                if now >= challenge["expires_at"]:
                    raise BootstrapError("claim_expired")
                return challenge
            challenge = {
                "schema": "zenith.resource-owner-challenge.v1",
                "binding": asdict(self.binding),
                "claim_id": claim_id,
                "nonce": secrets.token_hex(32),
                "issued_at": now,
                "expires_at": min(now + CHALLENGE_SECONDS, state["setup_expires_at"]),
                "admin_grant": {
                    "schema": "zenith.resource-admin-grant.v1",
                    "grant_id": grant_id or secrets.token_hex(32),
                    "holder_public_key": public_key,
                    "resource_id": self.binding.resource_id,
                    "operations": list(OPERATIONS),
                    "validity": {"mode": "until_revoked"},
                    "onward_delegation": False,
                },
            }
            state.update(state="prepared", challenge=challenge)
            self._save(db, state)
            return challenge

    def refresh_claim(self, secret, public_key, claim_id, *, now):
        """Explicitly renew only a review challenge, never the setup deadline.

        This is not called by prepare/redeem on expiry. The host must inspect
        retained state and obtain a new review before requesting a refresh.
        """
        _time(now)
        _hex(public_key, 64)
        _hex(claim_id, 32)
        with self._transaction() as db:
            state = self._load(db)
            if state["state"] == "active":
                raise BootstrapError("already_initialized")
            self._authorize(state, secret, now)
            if state["state"] != "prepared":
                raise BootstrapError("claim_not_prepared")
            challenge = state["challenge"]
            if (
                challenge["claim_id"] != claim_id
                or challenge["admin_grant"]["holder_public_key"] != public_key
            ):
                raise BootstrapError("claim_changed")
            if now < challenge["issued_at"]:
                raise BootstrapError("invalid_time")
            challenge.update(
                nonce=secrets.token_hex(32),
                issued_at=now,
                expires_at=min(now + CHALLENGE_SECONDS, state["setup_expires_at"]),
            )
            self._save(db, state)
            return challenge

    def redeem(self, secret, proof, *, now):
        _time(now)
        if (
            not isinstance(proof, dict)
            or set(proof) != {"schema", "challenge", "signature"}
            or proof["schema"] != "zenith.resource-owner-proof.v1"
        ):
            raise BootstrapError("invalid_proof")
        _hex(proof["signature"], 128)
        try:
            encoded = canonical(proof)
        except (ValueError, TypeError, OverflowError) as exc:
            raise BootstrapError("invalid_proof") from exc
        if len(encoded) > 16384:
            raise BootstrapError("proof_too_large")
        digest = hashlib.sha256(encoded).hexdigest()
        with self._transaction() as db:
            state = self._load(db)
            if state["state"] == "active":
                # A burned token cannot authorize anything new. Exact retained
                # proof recovery works after expiry without issuing another grant.
                if not hmac.compare_digest(
                    state["setup_digest"], self._secret_digest(secret)
                ) or not hmac.compare_digest(state["proof_digest"], digest):
                    raise BootstrapError("already_initialized")
                return dict(state["result"], duplicate=True)
            self._authorize(state, secret, now)
            if state["state"] != "prepared":
                raise BootstrapError("claim_not_prepared")
            challenge = state["challenge"]
            if canonical(proof["challenge"]) != canonical(challenge):
                raise BootstrapError("claim_changed")
            if not challenge["issued_at"] <= now < challenge["expires_at"]:
                raise BootstrapError("claim_expired")
            public_key = challenge["admin_grant"]["holder_public_key"]
            try:
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
                    bytes.fromhex(proof["signature"]),
                    OWNER_DOMAIN + canonical(challenge),
                )
            except (InvalidSignature, ValueError) as exc:
                raise BootstrapError("invalid_signature") from exc
            result = {
                "schema": "zenith.resource-owner-result.v1",
                "active": True,
                "owner_public_key": public_key,
                "admin_grant": challenge["admin_grant"],
                "duplicate": False,
            }
            db.execute(
                "INSERT INTO admin_grants VALUES (1, ?)",
                (canonical(challenge["admin_grant"]).decode(),),
            )
            db.execute(
                "INSERT INTO bootstrap_events(document) VALUES (?)",
                (
                    canonical(
                        {
                            "event": "first_admin_activated",
                            "at": now,
                            "proof": proof,
                            "setup_authorization_consumed": True,
                        }
                    ).decode(),
                ),
            )
            state.update(state="active", proof_digest=digest, result=result, consumed_at=now)
            self._save(db, state)
            if self.before_commit is not None:
                self.before_commit()
            return result

    def status(self, claim_id=None):
        with self._transaction() as db:
            state = self._load(db)
            if claim_id is not None and state.get("challenge", {}).get("claim_id") != claim_id:
                raise BootstrapError("unknown_claim")
            result = {"state": state["state"], "binding": state["binding"]}
            if state["state"] == "active":
                result["result"] = state["result"]
            return result

    def count_grants(self):
        with self._transaction() as db:
            return db.execute("SELECT COUNT(*) FROM admin_grants").fetchone()[0]


def active_owner(path, *, resource_id, grant_id, public_key):
    """Read a previously signed owner record; never arm, grant or renew.

    Genesis configuration hashes remain historical after the owner-approved
    connection publication. Runtime policy must bind the signed grant ID to the
    exact installation review and current resource, rather than re-bootstrap.
    """
    path = Path(path)
    try:
        if not path.is_absolute() or path.parent.resolve() != path.parent:
            raise BootstrapError("unsafe_store_path")
        BootstrapStore._private(path.parent, directory=True)
        BootstrapStore._private(path)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as db:
            db.execute("BEGIN")
            state_row = db.execute("SELECT document FROM bootstrap WHERE singleton=1").fetchone()
            grant_row = db.execute("SELECT document FROM admin_grants WHERE singleton=1").fetchone()
            events = db.execute(
                "SELECT document FROM bootstrap_events ORDER BY sequence LIMIT 2"
            ).fetchall()
            if (
                not state_row
                or not grant_row
                or len(events) != 1
                or any(len(row[0]) > 32768 for row in [state_row, grant_row, events[0]])
            ):
                raise BootstrapError("owner_registration_required")
            state, grant, event = (json.loads(row[0]) for row in [state_row, grant_row, events[0]])
        if (
            state.get("schema") != "zenith.owner-bootstrap-state.v1"
            or state.get("state") != "active"
        ):
            raise BootstrapError("owner_registration_required")
        binding = BootstrapBinding(**state["binding"])
        _hex(grant_id, 64)
        _hex(public_key, 64)
        expected = dict(
            schema="zenith.resource-admin-grant.v1",
            grant_id=grant_id,
            holder_public_key=public_key,
            resource_id=resource_id,
            operations=list(OPERATIONS),
            validity=dict(mode="until_revoked"),
            onward_delegation=False,
        )
        result = dict(
            schema="zenith.resource-owner-result.v1",
            active=True,
            owner_public_key=public_key,
            admin_grant=expected,
            duplicate=False,
        )
        proof = event["proof"]
        if (
            binding.resource_id != resource_id
            or canonical(grant) != canonical(expected)
            or canonical(state["challenge"]["admin_grant"]) != canonical(expected)
            or canonical(state["result"]) != canonical(result)
            or canonical(proof["challenge"]) != canonical(state["challenge"])
            or proof["schema"] != "zenith.resource-owner-proof.v1"
            or set(proof) != {"schema", "challenge", "signature"}
            or proof["challenge"]["binding"] != state["binding"]
            or hashlib.sha256(canonical(proof)).hexdigest() != state["proof_digest"]
            or event["setup_authorization_consumed"] is not True
        ):
            raise BootstrapError("owner_registration_required")
        _hex(proof["signature"], 128)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(proof["signature"]), OWNER_DOMAIN + canonical(proof["challenge"])
        )
        return dict(binding=state["binding"], result=result, proof_digest=state["proof_digest"])
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, InvalidSignature) as error:
        raise BootstrapError("owner_registration_required") from error
