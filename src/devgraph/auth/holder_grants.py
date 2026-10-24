"""Provider adapter over the shared native secS holder-grant contract.

No Python cryptographic signing/verifying fallback. Root status, assignment epoch
and replay use the same canonical provider database across all access points.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from devgraph.auth.access_policy import _draft_validity
from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_read import RESOURCE, _seconds


def checksum(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def checksum_bytes(path):
    import stat

    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 50000000:
        raise ValueError("unsafe native holder authority")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def permission(value):
    """Extract signed, portable path scope without weakening other constraints."""
    v = json.loads(canonical_json(value))
    paths = v["constraints"].pop("resource_paths", None)
    v["scope"] = (
        {"mode": "all"}
        if paths is None
        else {
            "mode": "paths",
            "roots": [
                [
                    dict(zip(("kind", "id"), anchor.split("/", 1), strict=True))
                    for anchor in p["path"]
                ]
                for p in paths
            ],
        }
    )
    return v


def constraint(value):
    v = json.loads(canonical_json(value))
    scope = v.pop("scope")
    if scope["mode"] == "paths":
        v["constraints"]["resource_paths"] = [
            {
                "schema": "devgraph.resource-path.v1",
                "path": [n["kind"] + "/" + n["id"] for n in root],
            }
            for root in scope["roots"]
        ]
    elif scope != {"mode": "all"}:
        raise UnauthenticatedError("unsupported_holder_scope")
    return v


class HolderGrantHost:
    def __init__(
        self,
        *,
        enrollment,
        binary: Path,
        binary_sha256: str,
        service: str,
        provider_enrollment=None,
    ):
        if not binary.is_absolute() or not binary.is_file() or not service or len(service) > 256:
            raise ValueError("explicit native grant authority and canonical service required")
        if not hasattr(enrollment, "_using_registry"):
            raise ValueError("holder grants require a durable member assignment registry")
        self.enrollment, self.binary, self.service = enrollment, binary, service
        self.provider = provider_enrollment or enrollment
        self.binary_sha256 = binary_sha256
        if checksum_bytes(binary) != binary_sha256:
            raise ValueError("native holder authority digest mismatch")
        authority = enrollment.authority.policy()
        self.pin = {k: authority[k] for k in ("issuer", "key_id", "public_key")}
        self.pin["service"] = service
        self.issuing = ContextVar("devgraph_holder_grant_issue", default=None)
        with self.provider._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS holder_roots (
                    id TEXT PRIMARY KEY, service TEXT NOT NULL, owner TEXT NOT NULL,
                    device TEXT NOT NULL, revision TEXT NOT NULL, digest TEXT NOT NULL,
                    request_digest TEXT NOT NULL, grant TEXT NOT NULL,
                    membership TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS holder_replay (
                    credential_id TEXT NOT NULL, nonce TEXT NOT NULL, expires INTEGER NOT NULL,
                    PRIMARY KEY(credential_id,nonce));
            """)
        enrollment.holder_grants = self

    def _provider_context(self):
        return (
            nullcontext()
            if self.provider._selection.get() is not None
            else self.provider._using_registry()
        )

    def _pin_current(self):
        policy = self.provider._authority_policy(self.provider.policy())
        if any(policy.get(k) != self.pin[k] for k in ("issuer", "key_id", "public_key")):
            raise UnauthenticatedError("holder_issuer_changed")

    def invoke(self, operation, **fields):
        self._pin_current()
        if checksum_bytes(self.binary) != self.binary_sha256:
            raise UnauthenticatedError("holder_authority_changed")
        with tempfile.TemporaryDirectory(prefix="devgraph-holder-authority-") as d:
            pin = Path(d).resolve() / "pin.json"
            fd = os.open(pin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(canonical_json(self.pin))
            args = [str(self.binary), "--issuer-pin-file", str(pin)]
            if operation == "issue":
                args += ["--issuer-key-file", str(self.enrollment.authority.issuer_key_file)]
            elif operation != "verify":
                raise UnauthenticatedError("unsupported_operation")
            try:
                result = subprocess.run(
                    args,
                    input=canonical_json(dict(operation=operation, **fields)),
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                if len(result.stdout) > 131072:
                    raise ValueError()
                value = json.loads(result.stdout)
                if value.get("ok") is not True or not isinstance(value.get("value"), dict):
                    raise ValueError()
                return value["value"]
            except (OSError, ValueError, subprocess.SubprocessError):
                raise UnauthenticatedError("holder_authority_denied") from None

    def revision(self, owner, device, current):
        e = self.provider
        assignment = json.loads(canonical_json(e._assignment(owner, device, current)))
        # Transport binding still belongs to old enrollment/consent. It is not
        # authority in the new provider/service grant contract.
        assignment.pop("receiver", None)
        generation = None
        provisioning = getattr(e, "provisioning", None)
        if provisioning is not None:
            with provisioning.inbox._database() as db:
                row = db.execute(
                    "SELECT request_id,active FROM access_assignments WHERE owner=? AND device=?",
                    (owner, device),
                ).fetchone()
                if row:
                    if not row[1]:
                        raise UnauthenticatedError("holder_grant_revoked")
                    action = db.execute(
                        "SELECT action_id FROM access_provisioning_audit WHERE request_id=? "
                        "AND operation='activate' ORDER BY rowid DESC LIMIT 1",
                        (row[0],),
                    ).fetchone()
                    if not action:
                        raise UnauthenticatedError("holder_activation_missing")
                    generation = action[0]
        return checksum(
            dict(assignment=assignment, epoch=e._epoch(owner, device), activation=generation)
        )

    def mint(self, manifest, current):
        e, original = self.enrollment, self.issuing.get()
        if original is None:
            raise UnauthenticatedError("grant_issue_context_required")
        owner, device = manifest["subject_public_key"], manifest["installation_id"]
        selected = e._policy_for(owner, device, current)
        authority = e._authority_policy(selected)
        assignment = e._assignment(owner, device, current)
        approved = self.provider._assignment(owner, device, current)
        original_assignment = json.loads(canonical_json(assignment))
        canonical_assignment = json.loads(canonical_json(approved))
        original_assignment.pop("receiver", None)
        canonical_assignment.pop("receiver", None)
        if original_assignment != canonical_assignment:
            raise UnauthenticatedError("canonical_assignment_required")
        claims = dict(
            schema="secs.holder-grant.v1",
            grant_id=original["id"],
            revision=self.revision(owner, device, current),
            issuer=self.pin["issuer"],
            key_id=self.pin["key_id"],
            service=self.service,
            holder_public_key=owner,
            installation_id=device,
            validity=json.loads(_draft_validity(assignment).to_json()),
            max_credential_lifetime_seconds=selected.lifetime_seconds,
            onward_delegation=False,
            permissions=[permission(p) for p in authority["permissions"]],
        )
        grant = self.invoke("issue", claims=claims, now=current)
        manifest.update(
            credential_id="dgrg_" + original["id"],
            holder_request_digest=original["digest"],
            holder_revision=claims["revision"],
        )
        return canonical_json(grant).decode()

    def _bind_provider(self, db):
        provider = self.provider
        namespace = "main"
        if provider.path.resolve() != self.enrollment.path.resolve():
            db.execute("ATTACH DATABASE ? AS holder_provider", (str(provider.path),))
            namespace = "holder_provider"
        if db.execute(f"PRAGMA {namespace}.journal_mode").fetchone() != ("delete",) or db.execute(
            f"PRAGMA {namespace}.synchronous"
        ).fetchone() != (2,):
            raise UnauthenticatedError("canonical_durability_required")
        snapshot = provider._registry()
        row = db.execute(
            f"SELECT revision,digest FROM {namespace}.member_policy_revision WHERE id=1"
        ).fetchone()
        if row != (snapshot.revision, checksum(snapshot)):
            raise UnauthenticatedError("canonical_policy_changed")
        provisioning = getattr(provider, "provisioning", None)
        if provisioning is not None:
            db.execute("ATTACH DATABASE ? AS holder_provisioning", (str(provisioning.inbox.path),))
            head = db.execute(
                "SELECT revision FROM holder_provisioning.access_provisioning_head WHERE id=1"
            ).fetchone()
            if head != (snapshot.revision,):
                raise UnauthenticatedError("canonical_policy_changed")
        return namespace

    def retain(self, db, manifest, credential, membership, current):
        namespace = self._bind_provider(db)
        owner, device = manifest["subject_public_key"], manifest["installation_id"]
        if manifest["holder_revision"] != self.revision(owner, device, current):
            raise UnauthenticatedError("canonical_policy_changed")
        grant = json.loads(credential)
        if db.execute(f"SELECT COUNT(*) FROM {namespace}.holder_roots").fetchone()[0] >= 10000:
            raise UnauthenticatedError("holder_grant_capacity")
        # An attached DELETE-journal transaction commits the root together with
        # consumption of the endpoint's original signed enrollment challenge.
        db.execute(
            f"INSERT INTO {namespace}.holder_roots VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                grant["claims"]["grant_id"],
                self.service,
                owner,
                device,
                manifest["holder_revision"],
                checksum(grant),
                manifest["holder_request_digest"],
                credential,
                canonical_json(membership).decode(),
                "active",
            ),
        )

    def revoke(self, credential_id):
        if not credential_id.startswith("dgrg_"):
            return
        with self.provider._database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE holder_roots SET status='revoked' WHERE (owner,device) IN "
                "(SELECT owner,device FROM holder_roots WHERE id=? AND service=?)",
                (credential_id[5:], self.service),
            )
            db.execute("COMMIT")

    def _recover(self, original, current):
        with self.provider._database() as db:
            db.execute("BEGIN")
            self.provider._check_policy_snapshot(db)
            row = db.execute(
                "SELECT service,owner,device,revision,digest,request_digest,"
                "grant,membership,status "
                "FROM holder_roots WHERE id=?",
                (original["id"],),
            ).fetchone()
            if row is None:
                return None
            self._current(row, current)
            if row[5] != original["digest"]:
                raise UnauthenticatedError("grant_request_changed")
            return dict(schema="devgraph.holder-grant-result.v1", grant=json.loads(row[6]))

    def issue(self, challenge, proof, signature, *, now=None):
        current = _seconds(now)
        original = dict(
            id=checksum(challenge)[:32],
            digest=checksum(dict(challenge=challenge, proof=proof, signature=signature)),
        )
        with self._provider_context():
            recovered = self._recover(original, current)
            if recovered is not None:
                return recovered
            token = self.issuing.set(original)
            try:
                result = self.enrollment.enroll(challenge, proof, signature, now=now)
                return dict(schema="devgraph.holder-grant-result.v1", grant=result["credential"])
            except UnauthenticatedError:
                # A concurrent identical request may have consumed the challenge.
                # Recover only its exact committed bytes, never sign a replacement.
                recovered = self._recover(original, current)
                if recovered is not None:
                    return recovered
                raise
            finally:
                self.issuing.reset(token)

    def _current(self, row, current):
        self._pin_current()
        if (
            row[0] != self.service
            or row[8] != "active"
            or row[3] != self.revision(row[1], row[2], current)
        ):
            raise UnauthenticatedError("holder_grant_revoked_or_changed")
        grant = json.loads(row[6])
        if checksum(grant) != row[4]:
            raise UnauthenticatedError("invalid_grant_record")
        if not self.provider.membership_check(json.loads(row[7]), row[1]):
            raise UnauthenticatedError("membership_required")

    def verify_request(self, *, credential, call, proof, now=None):
        current, e = _seconds(now), self.provider
        verified = self.invoke("verify", credential=credential, call=call, proof=proof, now=current)
        owner, device = verified["holder_public_key"], verified["installation_id"]
        with self._provider_context(), e._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                e._check_policy_snapshot(db)
                row = db.execute(
                    "SELECT service,owner,device,revision,digest,request_digest,"
                    "grant,membership,status "
                    "FROM holder_roots WHERE id=?",
                    (verified["grant_id"],),
                ).fetchone()
                if not row or row[4] != verified["grant_digest"]:
                    raise UnauthenticatedError("unknown_holder_grant")
                if row[1:3] != (owner, device):
                    raise UnauthenticatedError("holder_binding_changed")
                self._current(row, current)
                permissions = [constraint(p) for p in credential["claims"]["permissions"]]
                narrowed = []
                for permission_record in permissions:
                    for typed in e._decode_grants([permission_record]):
                        if typed.scope == "devgraph.work.read":
                            typed = typed.model_copy(
                                update={
                                    "operations": (
                                        permission_record["operation"].removeprefix(
                                            "devgraph.work."
                                        ),
                                    )
                                }
                            )
                        narrowed.append(typed)
                grants = tuple({canonical_json(g): g for g in narrowed}.values())
                db.execute("DELETE FROM holder_replay WHERE expires<=?", (current,))
                if db.execute("SELECT COUNT(*) FROM holder_replay").fetchone()[0] >= 10000:
                    raise UnauthenticatedError("try_later")
                if db.execute(
                    "SELECT 1 FROM holder_replay WHERE credential_id=? AND nonce=?",
                    (verified["credential_id"], proof["nonce"]),
                ).fetchone():
                    raise UnauthenticatedError("replay")
                db.execute(
                    "INSERT INTO holder_replay VALUES(?,?,?)",
                    (verified["credential_id"], proof["nonce"], proof["expires_at"]),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        identifier = verified["credential_id"]
        return AuthorityContext(
            envelope=CredentialEnvelope(
                actor_id=identifier,
                session_id=identifier,
                correlation_id=identifier,
                scopes=frozenset(g.scope for g in grants),
                expires_at=datetime.fromtimestamp(verified["expires_at"], timezone.utc),
                issuer=self.pin["issuer"],
                audience="devgraph",
                credential_id=identifier,
                credential_version=2,
                read_grants=grants,
                lifecycle_status="active",
                resource=RESOURCE,
            )
        )
