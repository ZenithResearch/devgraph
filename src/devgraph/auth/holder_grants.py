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
from contextlib import contextmanager, nullcontext
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
        if v["constraints"].get("scope") == "devgraph.arena.read":
            roots = scope["roots"]
            if not roots or any(len(root) != 1 or root[0]["kind"] != "Arena" for root in roots):
                raise UnauthenticatedError("unsupported_holder_scope")
            selected = {root[0]["id"] for root in roots}
            dynamic = v["constraints"].get("all_arenas") is True
            ids = v["constraints"].get("arena_ids", ())
            if not dynamic and not selected.issubset(ids):
                raise UnauthenticatedError("unsupported_holder_scope")
            v["constraints"].pop("all_arenas", None)
            v["constraints"]["arena_ids"] = sorted(selected)
        else:
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


def invoke_native(binary, binary_sha256, pin_value, operation, *, issuer_key_file=None, **fields):
    """Exact native crypto boundary, also usable for route-only recovery.

    The caller supplies its independently validated pin. This never constructs
    a grant/assignment or substitutes Python signature verification.
    """
    if checksum_bytes(binary) != binary_sha256:
        raise UnauthenticatedError("holder_authority_changed")
    with tempfile.TemporaryDirectory(prefix="devgraph-holder-authority-") as d:
        pin = Path(d).resolve() / "pin.json"
        fd = os.open(pin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(canonical_json(pin_value))
        args = [str(binary), "--issuer-pin-file", str(pin)]
        if operation in ("issue", "issue_v2", "issue_access_point", "issue_holder_session"):
            args += ["--issuer-key-file", str(issuer_key_file)]
        elif operation not in (
            "verify",
            "verify_v2",
            "verify_access_point",
            "check_selector",
            "check_effect",
            "review_holder_session",
            "verify_holder_session_authorization",
            "verify_holder_session",
            "verify_holder_session_status",
        ):
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


class HolderGrantHost:
    def __init__(
        self,
        *,
        enrollment,
        binary: Path,
        binary_sha256: str,
        service: str,
        provider_enrollment=None,
        grant_schema="secs.holder-grant.v1",
    ):
        if not binary.is_absolute() or not binary.is_file() or not service or len(service) > 256:
            raise ValueError("explicit native grant authority and canonical service required")
        if not hasattr(enrollment, "_using_registry"):
            raise ValueError("holder grants require a durable member assignment registry")
        if grant_schema not in ("secs.holder-grant.v1", "secs.holder-grant.v2"):
            raise ValueError("unsupported grant version")
        self.grant_schema = grant_schema
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
        return invoke_native(
            self.binary,
            self.binary_sha256,
            self.pin,
            operation,
            issuer_key_file=self.enrollment.authority.issuer_key_file,
            **fields,
        )

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
        selected_schema = original["grant_schema"]
        claims = dict(
            schema=selected_schema,
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
        if selected_schema == "secs.holder-grant.v2":
            from devgraph.auth.selector_grants import permission_v2

            claims["permissions"] = [
                permission_v2(p, self.service) for p in authority["permissions"]
            ]
        grant = self.invoke(
            "issue_v2" if selected_schema.endswith(".v2") else "issue", claims=claims, now=current
        )
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
            grant = json.loads(row[6])
            if grant["claims"]["schema"] != original["grant_schema"]:
                raise UnauthenticatedError("grant_schema_changed")
            return dict(schema="devgraph.holder-grant-result.v1", grant=grant)

    def issue(self, challenge, proof, signature, *, now=None):
        return self._issue(challenge, proof, signature, grant_schema=self.grant_schema, now=now)

    def issue_v2(self, challenge, proof, signature, *, now=None):
        """Explicit new enrollment, retaining current approved assignment unchanged."""
        return self._issue(
            challenge, proof, signature, grant_schema="secs.holder-grant.v2", now=now
        )

    def _issue(self, challenge, proof, signature, *, grant_schema, now=None):
        current = _seconds(now)
        original = dict(
            id=checksum(challenge)[:32],
            digest=checksum(dict(challenge=challenge, proof=proof, signature=signature)),
            grant_schema=grant_schema,
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

    @contextmanager
    def current_read(self, credential, proof, *, now=None):
        """Fence policy/revocation until the already-verified result is captured.

        Proof consumption happens first. This second check holds a shared
        DELETE-journal lock so revocation cannot commit during disclosure.
        """
        if credential.get("claims", {}).get("schema") == "secs.holder-session-credential.v1":
            sessions = getattr(self, "holder_sessions", None)
            if sessions is None:
                raise UnauthenticatedError("holder_sessions_not_enabled")
            with sessions.current_read(credential, proof, now=now):
                yield
            return
        grant = credential["grant"]
        with self._current_read_refs(
            grant["claims"]["grant_id"], checksum(grant), credential["claims"], proof, now=now
        ):
            yield

    @contextmanager
    def _current_read_refs(self, grant_id, grant_digest, claims, proof, *, now=None, extra=None):
        """Shared canonical disclosure fence for old credentials and finite sessions."""
        current = _seconds(now)
        with self._provider_context(), self.provider._database() as db:
            db.execute("BEGIN")
            self.provider._check_policy_snapshot(db)
            provisioning = getattr(self.provider, "provisioning", None)
            if provisioning is not None:
                db.execute(
                    "ATTACH DATABASE ? AS holder_read_policy", (str(provisioning.inbox.path),)
                )
                if db.execute("PRAGMA holder_read_policy.journal_mode").fetchone() != ("delete",):
                    raise UnauthenticatedError("canonical_durability_required")
                head = db.execute(
                    "SELECT revision FROM holder_read_policy.access_provisioning_head WHERE id=1"
                ).fetchone()
                if head != (self.provider._registry().revision,):
                    raise UnauthenticatedError("canonical_policy_changed")

            def check_clock():
                selected = _seconds(now)
                if not (
                    claims["issued_at"] <= selected < claims["expires_at"]
                    and proof["issued_at"] <= selected < proof["expires_at"]
                ):
                    raise UnauthenticatedError("holder_credential_expired")

            check_clock()
            if extra is not None:
                extra(db)
            row = db.execute(
                "SELECT service,owner,device,revision,digest,request_digest,"
                "grant,membership,status "
                "FROM holder_roots WHERE id=?",
                (grant_id,),
            ).fetchone()
            if row is None or row[4] != grant_digest:
                raise UnauthenticatedError("unknown_holder_grant")
            self._current(row, current)
            yield
            check_clock()
            if extra is not None:
                extra(db)
            self._current(row, _seconds(now))

    def verify_request(self, *, credential, call, proof, now=None):
        current = _seconds(now)
        v2 = credential.get("claims", {}).get("schema") == "secs.holder-credential.v2"
        verified = self.invoke(
            "verify_v2" if v2 else "verify",
            credential=credential,
            call=call,
            proof=proof,
            now=current,
        )
        return self._verified_context(verified, credential, proof, current, v2=v2)

    def _verified_context(self, verified, credential, proof, current, *, v2, unbound_parent=False):
        """Normalize only native-verified authority against the current canonical root."""
        e = self.provider
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
                if row[1] != owner or (
                    row[2] != device
                    and (
                        not unbound_parent
                        or json.loads(row[6])["claims"].get("installation_id") is not None
                    )
                ):
                    raise UnauthenticatedError("holder_binding_changed")
                self._current(row, current)
                permissions = (
                    verified["permissions"]
                    if v2
                    else [constraint(p) for p in credential["claims"]["permissions"]]
                )
                narrowed = []
                for permission_record in permissions:
                    decoder_record = (
                        {k: v for k, v in permission_record.items() if k != "roles"}
                        if v2
                        else permission_record
                    )
                    for typed in e._decode_grants([decoder_record]):
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
                        if v2:
                            from devgraph.auth.selector_grants import wrap_permission

                            narrowed.extend(wrap_permission(self, permission_record, [typed]))
                        else:
                            narrowed.append(typed)
                grants = (
                    tuple(narrowed)
                    if v2
                    else tuple({canonical_json(g): g for g in narrowed}.values())
                )
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
