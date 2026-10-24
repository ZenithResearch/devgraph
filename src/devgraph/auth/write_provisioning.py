"""Durable write assignments, bounded by separately installed native admission.

No issuer key, native policy, trust pin or live grant is installed here. An owner
must first admit the holder's native ceiling; operator consent only narrows it.
"""

import hashlib
import json
from contextlib import contextmanager
from typing import Literal

from pydantic import Field, model_validator

from devgraph.auth.access_policy import _Closed, _draft_validity, require_subset
from devgraph.auth.access_provisioning import ReadGrantProvisioning
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import AccessDraftV2
from devgraph.auth.write_scope import ReceiverWritePolicy, ScopedWriteGrant


class WriteAccessDraft(AccessDraftV2):
    schema_: Literal["devgraph.access-draft.v3"] = Field(alias="schema")
    write_grants: tuple[ScopedWriteGrant, ...] = Field(default=(), max_length=64)


class NativeRule(_Closed):
    actor_id: str = Field(pattern=r"^pubkey:sha256:[0-9a-f]{64}$")
    effect: Literal["allow", "deny"]
    status: Literal["active", "revoked"]
    operation: str = Field(min_length=1, max_length=128)
    resource: str = Field(pattern=r"^[A-Za-z]+/[a-z0-9-]*$", max_length=300)
    resource_match: Literal["exact", "prefix"]
    not_before: int = Field(strict=True, ge=0, le=9007199254740991)
    not_after: int = Field(strict=True, ge=1, le=9007199254740991)

    @model_validator(mode="after")
    def interval(self):
        if self.not_before >= self.not_after or (
            self.resource_match == "exact" and self.resource.endswith("/")
        ):
            raise ValueError("invalid native rule interval or resource")
        return self


class NativeWritePolicy(_Closed):
    schema_: Literal["secs-devgraph-work-policy.v1"] = Field(alias="schema")
    schema_version: int = Field(strict=True, ge=1, le=1)
    audience: Literal["devgraph://receiver-local"]
    policy_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    policy_version: int = Field(strict=True, ge=1, le=9007199254740991)
    rules: tuple[NativeRule, ...] = Field(min_length=1, max_length=1000)

    @property
    def digest(self):
        return hashlib.sha256(b"secs-devgraph-work-policy.v1\0" + canonical_json(self)).hexdigest()

    def require(self, draft):
        actor = (
            "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(draft.subject_public_key)).hexdigest()
        )
        for grant in draft.write_grants:
            for resource in grant.resources:
                for identifier in resource.ids or (None,):
                    selected = resource.kind + "/" + (identifier or "")
                    allowed = False
                    for rule in self.rules:
                        if (
                            rule.status != "active"
                            or rule.actor_id != actor
                            or rule.operation != grant.operation
                        ):
                            continue
                        covers = (
                            (
                                rule.resource_match == "prefix"
                                and selected.startswith(rule.resource)
                                if identifier is not None
                                else rule.resource_match == "prefix" and rule.resource == selected
                            )
                            or identifier is not None
                            and rule.resource == selected
                        )
                        overlaps = covers or (
                            identifier is None and rule.resource.startswith(selected)
                        )
                        if (
                            rule.effect == "deny"
                            and overlaps
                            and (
                                (
                                    _draft_validity(draft).not_after is None
                                    or rule.not_before < draft.not_after
                                )
                                and (
                                    _draft_validity(rule).not_after is None
                                    or rule.not_after > draft.not_before
                                )
                            )
                        ):
                            raise UnauthenticatedError("native_admission_required")
                        if (
                            rule.effect == "allow"
                            and covers
                            and _draft_validity(draft).contained_by(_draft_validity(rule))
                        ):
                            allowed = True
                    if not allowed:
                        raise UnauthenticatedError("native_admission_required")


class WriteGrantProvisioning(ReadGrantProvisioning):
    write_enabled = True
    receiver_policy_type = ReceiverWritePolicy
    receiver_policy_schema = "devgraph.receiver-write-policy.v1"

    @staticmethod
    def decode_write_draft(raw):
        return WriteAccessDraft.model_validate_json(raw)

    def __init__(self, *, operators, native, **kwargs):
        self.operators, self.native = operators, native
        self._initializing = True
        super().__init__(**kwargs)
        with self.inbox._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS write_provisioning_head (
                    id INTEGER PRIMARY KEY CHECK(id=1), native_id TEXT NOT NULL,
                    native_version INTEGER NOT NULL, native_digest TEXT NOT NULL,
                    operator_revision INTEGER NOT NULL, operator_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS write_assignments (
                    owner TEXT PRIMARY KEY, device TEXT NOT NULL, request_id TEXT NOT NULL,
                    assignment TEXT NOT NULL, digest TEXT NOT NULL, active INTEGER NOT NULL);
            """)
        self._initializing = False
        self.refresh()

    def _configuration(self):
        operator, native = self.operators(), self.native()
        if not isinstance(operator.ceiling, WriteAccessDraft) or not isinstance(
            native, NativeWritePolicy
        ):
            raise UnauthenticatedError("write_configuration_required")
        if (
            operator.ceiling.issuer != self.inbox.issuer
            or operator.ceiling.receiver != self.inbox.receiver
        ):
            raise UnauthenticatedError("write_configuration_changed")
        return operator, native

    @staticmethod
    def _head(operator, native):
        return (
            native.policy_id,
            native.policy_version,
            native.digest,
            operator.revision,
            operator.digest,
        )

    def refresh(self):
        base = super().refresh()
        if self._initializing:
            return base
        operator, native = self._configuration()
        new = self._head(operator, native)
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM write_provisioning_head WHERE id=1").fetchone()
            if old:
                old = old[1:]
                if (
                    old[0] != new[0]
                    or new[1] < old[1]
                    or new[3] < old[3]
                    or new[1] == old[1]
                    and new[2] != old[2]
                    or new[3] == old[3]
                    and new[4] != old[4]
                ):
                    raise UnauthenticatedError("write_policy_rollback")
            # Observe narrowing permanently, even when this call is later denied.
            for owner, raw, checksum in db.execute(
                "SELECT owner,assignment,digest FROM write_assignments WHERE active=1"
            ).fetchall():
                grant = self._decode(owner, raw, checksum)
                try:
                    self._eligible(grant, base, operator, native)
                except (ValueError, UnauthenticatedError):
                    db.execute("UPDATE write_assignments SET active=0 WHERE owner=?", (owner,))
            db.execute("INSERT OR REPLACE INTO write_provisioning_head VALUES(1,?,?,?,?,?)", new)
            db.execute("COMMIT")
        return base

    def _write_configuration(self, db):
        base = self._base(db)
        operator, native = self._configuration()
        old = db.execute("SELECT * FROM write_provisioning_head WHERE id=1").fetchone()
        if old is None or old[1:] != self._head(operator, native):
            raise UnauthenticatedError("write_policy_changed")
        return base, operator, native

    @staticmethod
    def _eligible(grant, base, operator, native):
        if grant.read_grants or not grant.write_grants or grant.installation_id is not None:
            raise UnauthenticatedError("unsupported_write_binding")
        require_subset(grant, base.ceiling)
        require_subset(grant, operator.ceiling)
        native.require(grant)

    def _review_eligible(self, grant, base, operator, native):
        self._eligible(grant, base, operator, native)

    def _activate_native(self, db, grant, actor, action_id):
        # Legacy runtimes require prior explicit admission; no authority setup.
        return None

    @classmethod
    def _decode(cls, owner, raw, checksum):
        grant = cls.decode_write_draft(raw)
        if grant.subject_public_key != owner or grant.digest != checksum:
            raise UnauthenticatedError("write_registry_invalid")
        return grant

    def write_current(self, db, owner):
        self._write_configuration(db)
        row = db.execute(
            "SELECT assignment,digest,active FROM write_assignments WHERE owner=?", (owner,)
        ).fetchone()
        return self._decode(owner, row[0], row[1]) if row and row[2] else None

    def review_write(self, grant):
        with self.inbox._database() as db:
            base, operator, native = self._write_configuration(db)
            self._review_eligible(grant, base, operator, native)
        return dict(
            schema="devgraph.write-admission-plan.v1",
            native_policy_digest=native.digest,
            receiver_scope=grant.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        )

    def preview(self, db, selection, record, previous_digest, now):
        if selection.write is None:
            return super().preview(db, selection, record, previous_digest, now)
        if selection.read is not None:
            raise UnauthenticatedError("mixed_activation_unavailable")
        grant = selection.write
        if not isinstance(grant, WriteAccessDraft) or grant.subject_public_key != record[1]:
            raise UnauthenticatedError("write_activation_upgrade_required")
        base, operator, native = self._write_configuration(db)
        self._review_eligible(grant, base, operator, native)
        current = self.write_current(db, record[1])
        if previous_digest != (current.digest if current else None):
            raise UnauthenticatedError("assignment_changed")
        if not _draft_validity(grant).contains(now):
            raise UnauthenticatedError("assignment_expired")
        if (
            db.execute("SELECT 1 FROM write_assignments WHERE owner=?", (record[1],)).fetchone()
            is None
            and db.execute("SELECT count(*) FROM write_assignments").fetchone()[0] >= 128
        ):
            raise UnauthenticatedError("provisioning_capacity")
        return dict(
            schema="devgraph.write-activation-plan.v1",
            assignment_digest=grant.digest,
            previous_assignment_digest=previous_digest,
            subject_public_key=record[1],
            installation_id=record[2],
            holder_wide=True,
            expires_at=grant.not_after,
            **(
                {"validity": json.loads(grant.validity.to_json())}
                if grant.schema_ == "devgraph.access-draft.v5"
                else {}
            ),
            replaces_existing=current is not None,
            native_policy_digest=native.digest,
        )

    def activate(self, db, selection, record, previous_digest, actor, action_id, now):
        if selection.write is None:
            return super().activate(db, selection, record, previous_digest, actor, action_id, now)
        plan = self.preview(db, selection, record, previous_digest, now)
        grant = selection.write
        self._activate_native(db, grant, actor, action_id)
        db.execute(
            "INSERT OR REPLACE INTO write_assignments VALUES(?,?,?,?,?,1)",
            (record[1], record[2], record[0], canonical_json(grant).decode(), grant.digest),
        )
        db.execute(
            "INSERT INTO access_provisioning_audit VALUES(?,?,?,?,?,?)",
            (action_id, record[0], "write.activate", grant.digest, actor, now),
        )
        return dict(plan, active=True, credential_issued=False)

    def revoke_write(self, db, record, previous_digest, actor, action_id, now):
        current = self.write_current(db, record[1])
        row = db.execute(
            "SELECT request_id,digest,active FROM write_assignments WHERE owner=?", (record[1],)
        ).fetchone()
        if not current or row != (record[0], previous_digest, 1):
            raise UnauthenticatedError("assignment_changed")
        db.execute("UPDATE write_assignments SET active=0 WHERE owner=?", (record[1],))
        db.execute(
            "INSERT INTO access_provisioning_audit VALUES(?,?,?,?,?,?)",
            (action_id, record[0], "write.revoke", current.digest, actor, now),
        )
        return dict(
            assignment_digest=current.digest,
            active=False,
            credential_issued=False,
            holder_wide=True,
        )

    def summaries(self, db, policy, now):
        result = super().summaries(db, policy, now)
        for owner, device, request_id, raw, checksum in db.execute(
            "SELECT owner,device,request_id,assignment,digest FROM write_assignments "
            "WHERE active=1 ORDER BY request_id"
        ):
            grant = self._decode(owner, raw, checksum)
            try:
                require_subset(grant, policy.ceiling)
            except ValueError:
                continue
            if _draft_validity(grant).contains(now):
                record = db.execute(
                    "SELECT digest FROM access_requests WHERE id=?", (request_id,)
                ).fetchone()
                result.append(
                    dict(
                        request_id=request_id,
                        request_digest_sha256=record[0],
                        subject_public_key=owner,
                        installation_id=device,
                        assignment_digest=checksum,
                        expires_at=grant.not_after,
                        **(
                            {"validity": json.loads(grant.validity.to_json())}
                            if grant.schema_ == "devgraph.access-draft.v5"
                            else {}
                        ),
                        version=2,
                        access_mode="write",
                        holder_wide=True,
                    )
                )
        return result

    def request_result(self, db, owner, request_id, now):
        row = db.execute(
            "SELECT request_id,assignment,active FROM write_assignments WHERE owner=?", (owner,)
        ).fetchone()
        decision = db.execute(
            "SELECT selection FROM access_decisions WHERE request_id=?", (request_id,)
        ).fetchone()
        selected = json.loads(decision[0]) if decision and decision[0] else {}
        if not (selected or {}).get("write"):
            return None
        state = "revoked"
        if row and row[0] != request_id:
            state = "superseded"
        elif row and row[2]:
            grant = self.decode_write_draft(row[1])
            state = "active" if _draft_validity(grant).contains(now) else "expired"
        return dict(
            schema="devgraph.access-request-result.v3", status=state, grant_active=state == "active"
        )

    @staticmethod
    def receiver_assignment(grant, owner):
        return dict(
            actor_id="pubkey:sha256:" + hashlib.sha256(bytes.fromhex(owner)).hexdigest(),
            not_before=grant.not_before,
            not_after=grant.not_after,
            grants=grant.write_grants,
        )

    @contextmanager
    def admission(self, *, stable_issuer):
        self.refresh()
        with self.inbox._database() as db:
            db.execute("BEGIN")
            base, operator, native = self._write_configuration(db)
            assignments = []
            for owner, raw, checksum in db.execute(
                "SELECT owner,assignment,digest FROM write_assignments WHERE active=1"
            ):
                grant = self._decode(owner, raw, checksum)
                self._eligible(grant, base, operator, native)
                assignments.append(self.receiver_assignment(grant, owner))
            policy = self.receiver_policy_type.model_validate(
                dict(
                    schema=self.receiver_policy_schema,
                    revision=operator.revision,
                    stable_issuer=stable_issuer,
                    audience=native.audience,
                    native_policy_id=native.policy_id,
                    native_policy_version=native.policy_version,
                    native_policy_digest_sha256=native.digest,
                    assignments=assignments,
                )
            )
            # Keep this shared SQLite lock until native preparation or the entire
            # graph transaction finishes. Revocation cannot slip into admission.
            yield policy


class ProvisionedWriteReceiver:
    def __init__(self, *, receiver, provisioning):
        self.receiver, self.provisioning = receiver, provisioning

    def _load_verifier(self):
        return self.receiver._load_verifier()

    def _run(self, operation, arguments):
        from devgraph.auth.write_scope import ScopedSecSWorkAdapter
        from devgraph.ops.named_work_v2_receiver import _CurrentReceiverVerifier

        current = self._load_verifier().config
        with self.provisioning.admission(stable_issuer=current.stable_issuer) as policy:
            adapter = ScopedSecSWorkAdapter(
                storage=self.receiver.storage,
                audit_log=self.receiver.audit_log,
                verifier=_CurrentReceiverVerifier(self._load_verifier),
                policy=lambda: policy,
                clock=current.clock,
            )
            return getattr(adapter, operation)(**arguments)

    def execute(self, **arguments):
        return self._run("execute", arguments)

    def status(self, **arguments):
        return self._run("status", arguments)

    @contextmanager
    def preflight(self, holder, request):
        from types import SimpleNamespace

        current = self._load_verifier().config
        with self.provisioning.admission(stable_issuer=current.stable_issuer) as policy:
            native = self.provisioning.native()
            verified = SimpleNamespace(
                request=request,
                principal=SimpleNamespace(
                    issuer=current.stable_issuer,
                    audience=current.audience,
                    actor_id="pubkey:sha256:" + hashlib.sha256(bytes.fromhex(holder)).hexdigest(),
                ),
                safe_summary=dict(
                    receiver_policy_id=native.policy_id,
                    receiver_policy_version=native.policy_version,
                    receiver_policy_digest_sha256=native.digest,
                ),
            )
            scope = policy.authorize(verified, now=current.clock())
            with self.receiver.storage.work_mutation_transaction():
                scope.prepare(self.receiver.storage, request)
            yield
