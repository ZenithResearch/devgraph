"""Independently authorized review, with explicitly composed read activation."""

import hashlib
import json
import re
import secrets
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from devgraph.auth.access_approval import WalletAccessChallenge, verify_wallet_approval
from devgraph.auth.access_policy import _draft_validity, compile_write, require_subset
from devgraph.auth.access_requests import Closed
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import AccessDraftV2, compile_monitor_read
from devgraph.auth.native_read import _seconds
from devgraph.auth.write_provisioning import WriteAccessDraft


class OperatorPolicy(Closed):
    schema_: Literal["devgraph.access-operator-policy.v1"] = Field(alias="schema")
    revision: int = Field(strict=True, ge=1, le=9007199254740991)
    operator_public_keys: tuple[str, ...] = Field(default=(), max_length=32)
    ceiling: AccessDraftV2

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.operator_public_keys)) != len(self.operator_public_keys) or any(
            not re.fullmatch(r"[0-9a-f]{64}", key) for key in self.operator_public_keys
        ):
            raise ValueError("invalid operator policy")
        if len(canonical_json(self.ceiling)) > 65536:
            raise ValueError("operator ceiling is too large")
        return self

    @property
    def digest(self):
        return hashlib.sha256(canonical_json(self)).hexdigest()


class OperatorPolicyV2(OperatorPolicy):
    schema_: Literal["devgraph.access-operator-policy.v2"] = Field(alias="schema")
    ceiling: WriteAccessDraft


class GrantSelection(Closed):
    read: AccessDraftV2 | None = None
    write: WriteAccessDraft | AccessDraftV2 | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.read is None and self.write is None:
            raise ValueError("select at least one permission")
        return self


class OperatorAction(Closed):
    scoped_write_schema: ClassVar[str] = "devgraph.access-operator-action.v3"
    schema_: Literal[
        "devgraph.access-operator-action.v1",
        "devgraph.access-operator-action.v2",
        "devgraph.access-operator-action.v3",
    ] = Field(alias="schema")
    action: Literal["inbox", "review", "approve", "deny", "status", "revoke"]
    action_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    original_action_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_version: int | None = Field(default=None, strict=True, ge=1)
    request_digest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    selection: GrantSelection | None = None
    review_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    reason: str | None = Field(default=None, min_length=1, max_length=512)
    target_public_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    target_installation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    previous_assignment_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    after_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def valid(self):
        if (
            self.selection
            and self.selection.write is not None
            and (
                isinstance(self.selection.write, WriteAccessDraft)
                != (self.schema_ == self.scoped_write_schema)
            )
        ):
            raise ValueError("write draft and action versions must match")
        targets = {"target_public_key", "target_installation_id"} & self.model_fields_set
        if self.action == "revoke":
            if len(targets) != 2 or not self.target_public_key or not self.target_installation_id:
                raise ValueError("revocation needs an exact target member and installation")
        elif targets:
            raise ValueError("revocation target is not valid for this action")
        changed = "previous_assignment_digest" in self.model_fields_set
        if self.schema_ == "devgraph.access-operator-action.v1" and (
            changed or self.action == "revoke"
        ):
            raise ValueError("activation needs the v2 action contract")
        if self.schema_ != "devgraph.access-operator-action.v1":
            if (self.action in ("review", "approve", "revoke")) != changed:
                raise ValueError("an exact previous assignment is required for activation")
            if self.action == "revoke" and not self.previous_assignment_digest:
                raise ValueError("revoke needs the exact active assignment")
        if self.action in ("inbox", "status"):
            if (self.action == "status") != bool(self.original_action_id):
                raise ValueError("status needs the original action ID")
            if any(
                (
                    self.request_id,
                    self.expected_version,
                    self.selection,
                    self.review_id,
                    self.reason,
                    self.request_digest_sha256,
                )
            ):
                raise ValueError("invalid inbox action")
        else:
            if (
                not self.request_id
                or self.expected_version is None
                or not self.request_digest_sha256
            ):
                raise ValueError("exact request and version required")
            if self.after_id or self.limit != 50:
                raise ValueError("pagination belongs to inbox")
            if self.action in ("review", "approve"):
                if not self.selection or self.reason:
                    raise ValueError("selection required")
                if (self.action == "approve") != bool(self.review_id):
                    raise ValueError("approval requires a prior exact review")
            elif self.selection or self.review_id or not self.reason:
                raise ValueError("denial requires a reason")
        return self

    def wire(self):
        value = self.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        if self.schema_ != "devgraph.access-operator-action.v1" and self.action in (
            "review",
            "approve",
            "revoke",
        ):
            value["previous_assignment_digest"] = self.previous_assignment_digest
        return value


class AccessOperator:
    write_action_schemas = ("devgraph.access-operator-action.v3",)

    @staticmethod
    def parse_action(value):
        return OperatorAction.model_validate(value)

    @staticmethod
    def compile_read(draft):
        return compile_monitor_read(draft)

    def decorate_inbox(self, result, policy):
        return result

    @staticmethod
    def previous_assignments(action):
        return action.previous_assignment_digest

    def revoke_selection(self, db, action, record, policy, owner, current):
        writing = action.schema_ in self.write_action_schemas
        grant = (
            self.provisioning.write_current(db, record[1])
            if writing
            else self.provisioning.current(db, *record[1:3])
        )
        if grant is None:
            raise UnauthenticatedError("assignment_changed")
        require_subset(grant, policy.ceiling)
        revoke = self.provisioning.revoke_write if writing else self.provisioning.revoke
        return revoke(
            db, record, action.previous_assignment_digest, owner, action.action_id, current
        )

    def __init__(self, *, inbox, policy, inventory=None, provisioning=None):
        self.inbox, self.policy, self.inventory = inbox, policy, inventory
        self.provisioning = provisioning
        current = policy()
        if current.ceiling.issuer != inbox.issuer or current.ceiling.receiver != inbox.receiver:
            raise ValueError("operator policy receiver mismatch")
        with inbox._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS access_operator_denials (
                    id TEXT PRIMARY KEY, reason TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS access_operator_head (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER, digest TEXT);
                CREATE TABLE IF NOT EXISTS access_operator_challenges (
                    id TEXT PRIMARY KEY, owner TEXT, payload TEXT,
                    expires INTEGER, used INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS access_operator_results (
                    action_id TEXT PRIMARY KEY, owner TEXT, digest TEXT, result TEXT);
                CREATE TABLE IF NOT EXISTS access_operator_audit (
                    action_id TEXT PRIMARY KEY, owner TEXT, operation TEXT,
                    digest TEXT, created INTEGER);
                CREATE TABLE IF NOT EXISTS access_decisions (
                    request_id TEXT PRIMARY KEY, version INTEGER, status TEXT,
                    action_id TEXT, owner TEXT, selection TEXT, reason TEXT, created INTEGER);
            """)
        self._refresh_policy()

    def _refresh_policy(self):
        if self.provisioning is not None:
            self.provisioning.refresh()
        policy = self.policy()
        if (
            policy.ceiling.issuer != self.inbox.issuer
            or policy.ceiling.receiver != self.inbox.receiver
        ):
            raise UnauthenticatedError("operator_policy_changed")
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT revision,digest FROM access_operator_head WHERE id=1"
            ).fetchone()
            if old and (
                policy.revision < old[0] or policy.revision == old[0] and policy.digest != old[1]
            ):
                raise UnauthenticatedError("operator_policy_rollback")
            db.execute(
                "INSERT OR REPLACE INTO access_operator_head VALUES(1,?,?)",
                (policy.revision, policy.digest),
            )
            db.execute("COMMIT")
        return policy

    def _current(self, db):
        policy = self.policy()
        head = db.execute("SELECT revision,digest FROM access_operator_head WHERE id=1").fetchone()
        if head != (policy.revision, policy.digest):
            raise UnauthenticatedError("operator_policy_changed")
        if (
            policy.ceiling.issuer != self.inbox.issuer
            or policy.ceiling.receiver != self.inbox.receiver
        ):
            raise UnauthenticatedError("operator_policy_changed")
        return policy

    def record_denial(self, reason):
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM access_operator_denials").fetchone()[0] >= 10000:
                import sqlite3

                raise sqlite3.OperationalError("operator audit capacity exceeded")
            db.execute(
                "INSERT INTO access_operator_denials VALUES(?,?,?)",
                (secrets.token_hex(16), reason, _seconds(None)),
            )
            db.execute("COMMIT")

    def _audited(self, operation, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (UnauthenticatedError, ValueError, TypeError) as error:
            self.record_denial(operation + "_rejected")
            error._access_operator_audited = True
            raise
        except RuntimeError:
            self.record_denial(operation + "_failed")
            raise

    def _admit_action(self, action):
        modern = action.schema_ != "devgraph.access-operator-action.v1"
        writes = getattr(self.provisioning, "write_enabled", False)
        if modern and self.provisioning is None:
            raise UnauthenticatedError("activation_unavailable")
        if action.schema_ in self.write_action_schemas and not writes:
            raise UnauthenticatedError("write_activation_unavailable")
        if self.provisioning is not None:
            if action.action in ("review", "approve", "revoke") and not modern:
                raise UnauthenticatedError("activation_upgrade_required")
            if action.selection and action.selection.write is not None:
                if not writes:
                    raise UnauthenticatedError("write_activation_unavailable")
                if action.schema_ not in self.write_action_schemas:
                    raise UnauthenticatedError("write_activation_upgrade_required")
                if action.selection.read is not None and not getattr(
                    self.provisioning, "mixed_enabled", False
                ):
                    raise UnauthenticatedError("mixed_activation_unavailable")
                self.provisioning.review_write(action.selection.write)
            elif action.schema_ in self.write_action_schemas and action.action in (
                "review",
                "approve",
            ):
                raise UnauthenticatedError("write_activation_upgrade_required")

    def challenge(self, owner, device, action: OperatorAction, *, now=None):
        return self._audited("challenge", self._challenge, owner, device, action, now=now)

    def _challenge(self, owner, device, action: OperatorAction, *, now=None):
        self._refresh_policy()
        current = _seconds(now)
        if (
            not isinstance(owner, str)
            or not re.fullmatch(r"[0-9a-f]{64}", owner)
            or not isinstance(device, str)
            or not re.fullmatch(r"[0-9a-f]{32}", device)
        ):
            raise UnauthenticatedError("operator_required")
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self._current(db)
            if not _draft_validity(policy.ceiling).contains(current):
                raise UnauthenticatedError("operator_policy_expired")
            if owner not in policy.operator_public_keys:
                raise UnauthenticatedError("operator_required")
            self._admit_action(action)
            details = dict(
                action=action.wire(),
                policy_revision=policy.revision,
                policy_digest_sha256=policy.digest,
            )
            operation = {
                "inbox": "access.inbox",
                "review": "access.review",
                "approve": "access.decide",
                "deny": "access.decide",
                "status": "access.status",
                "revoke": "access.decide",
            }[action.action]
            value = dict(
                schema="castalia.access-request-challenge.v1",
                challenge_id=secrets.token_hex(16),
                nonce=secrets.token_urlsafe(32),
                issuer=self.inbox.issuer,
                receiver=self.inbox.receiver,
                subject_public_key=owner,
                installation_id=device,
                issued_at=current,
                expires_at=current + 120,
                operation=operation,
                details=details,
            )
            WalletAccessChallenge.model_validate(value)
            db.execute("DELETE FROM access_operator_challenges WHERE expires<=?", (current,))
            if db.execute("SELECT count(*) FROM access_operator_challenges").fetchone()[0] >= 256:
                raise UnauthenticatedError("try_later")
            # No request metadata/ceiling is returned before holder authentication.
            db.execute(
                "INSERT INTO access_operator_challenges VALUES(?,?,?,?,0)",
                (value["challenge_id"], owner, canonical_json(value).decode(), current + 120),
            )
            db.execute("COMMIT")
        return value

    def _selection(self, selection, record, policy, current):
        if selection is None:
            raise UnauthenticatedError("selection_required")
        plans = {}
        for kind, draft in (("read", selection.read), ("write", selection.write)):
            if draft is None:
                continue
            require_subset(draft, policy.ceiling)
            if (
                draft.previous_digest != policy.ceiling.digest
                or draft.subject_public_key != record[1]
                or not _draft_validity(draft).contains(current)
            ):
                raise UnauthenticatedError("selection_binding_mismatch")
            if kind == "read":
                if draft.write_grants or draft.installation_id != record[2]:
                    raise UnauthenticatedError("selection_binding_mismatch")
                plans[kind] = self.compile_read(draft).model_dump(
                    mode="json", by_alias=True, exclude_defaults=True
                )
            else:
                if draft.read_grants or draft.installation_id is not None:
                    raise UnauthenticatedError("unsupported_write_binding")
                if getattr(self.provisioning, "write_enabled", False):
                    plans[kind] = self.provisioning.review_write(draft)
                else:
                    plans[kind] = compile_write(
                        draft, policy_id="selected-work", policy_version=policy.revision
                    )
        return plans

    def execute(self, challenge, signature, *, now=None):
        return self._audited("action", self._execute, challenge, signature, now=now)

    def _execute(self, challenge, signature, *, now=None):
        self._refresh_policy()
        current = _seconds(now)
        parsed = verify_wallet_approval(
            challenge,
            signature,
            issuer=self.inbox.issuer,
            receiver=self.inbox.receiver,
            current=current,
        )
        details = parsed.details
        if set(details) != {"action", "policy_revision", "policy_digest_sha256"}:
            raise UnauthenticatedError("invalid_operator_action")
        action = self.parse_action(details["action"])
        expected = {
            "inbox": "access.inbox",
            "review": "access.review",
            "approve": "access.decide",
            "deny": "access.decide",
            "status": "access.status",
            "revoke": "access.decide",
        }[action.action]
        if parsed.operation != expected:
            raise UnauthenticatedError("invalid_operator_action")
        digest = hashlib.sha256(canonical_json(details)).hexdigest()
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self._current(db)
            if not _draft_validity(policy.ceiling).contains(current):
                raise UnauthenticatedError("operator_policy_expired")
            if (
                parsed.subject_public_key not in policy.operator_public_keys
                or details["policy_revision"] != policy.revision
                or details["policy_digest_sha256"] != policy.digest
            ):
                raise UnauthenticatedError("operator_policy_changed")
            self._admit_action(action)
            row = db.execute(
                "SELECT payload,used FROM access_operator_challenges WHERE id=?",
                (parsed.challenge_id,),
            ).fetchone()
            if not row or row[0].encode() != canonical_json(challenge):
                raise UnauthenticatedError("invalid_operator_challenge")
            old = db.execute(
                "SELECT owner,digest,result FROM access_operator_results WHERE action_id=?",
                (action.action_id,),
            ).fetchone()
            if old:
                if old[:2] != (parsed.subject_public_key, digest):
                    raise UnauthenticatedError("operator_action_conflict")
                result = json.loads(old[2])
            else:
                if (
                    row[1]
                    or db.execute("SELECT count(*) FROM access_operator_results").fetchone()[0]
                    >= 1024
                ):
                    raise UnauthenticatedError("try_later")
                if action.action == "status":
                    original = db.execute(
                        "SELECT owner,result FROM access_operator_results WHERE action_id=?",
                        (action.original_action_id,),
                    ).fetchone()
                    if original and original[0] != parsed.subject_public_key:
                        raise UnauthenticatedError("operator_result_unavailable")
                    result = dict(
                        schema="devgraph.access-operator-result.v1",
                        action_id=action.action_id,
                        original_action_id=action.original_action_id,
                        status="reconciled" if original else "unknown",
                        result=json.loads(original[1]) if original else None,
                        authority_issued=False,
                    )
                elif action.action == "inbox":
                    predicate = (
                        "expires>? AND NOT EXISTS (SELECT 1 FROM access_decisions d "
                        "WHERE d.request_id=access_requests.id)"
                    )
                    total = db.execute(
                        "SELECT count(*) FROM access_requests WHERE " + predicate, (current,)
                    ).fetchone()[0]
                    records = db.execute(
                        "SELECT * FROM access_requests WHERE "
                        + predicate
                        + " AND id>? ORDER BY id LIMIT ?",
                        (current, action.after_id or "", action.limit + 1),
                    ).fetchall()
                    items = [
                        dict(
                            self.inbox._result(r, current),
                            subject_public_key=r[1],
                            installation_id=r[2],
                            intent=json.loads(r[4]),
                            version=1,
                        )
                        for r in records[: action.limit]
                    ]
                    result = dict(
                        schema="devgraph.access-operator-result.v1",
                        action_id=action.action_id,
                        inbox=dict(
                            total=total,
                            items=items,
                            next_cursor=records[action.limit - 1][0]
                            if len(records) > action.limit
                            else None,
                        ),
                        ceiling_digest_sha256=policy.ceiling.digest,
                        ceiling=policy.ceiling.model_dump(
                            mode="json", by_alias=True, exclude_defaults=True
                        ),
                        policy_revision=policy.revision,
                        policy_digest_sha256=policy.digest,
                        authority_issued=False,
                    )
                    if self.provisioning is not None:
                        result["activation"] = {
                            "schema": "devgraph.access-activation-capabilities.v1",
                            "read": True,
                            "write": getattr(self.provisioning, "write_enabled", False),
                        }
                        if getattr(self.provisioning, "write_enabled", False):
                            result["activation"].update(
                                schema="devgraph.access-activation-capabilities.v2",
                                mixed=False,
                                write_action_schema="devgraph.access-operator-action.v3",
                            )
                        result["active_grants"] = self.provisioning.summaries(db, policy, current)
                        for item in result["inbox"]["items"]:
                            existing = self.provisioning.current(
                                db, item["subject_public_key"], item["installation_id"]
                            )
                            item["previous_assignment_digest"] = (
                                existing.digest if existing else None
                            )
                            if getattr(self.provisioning, "write_enabled", False):
                                previous_write = self.provisioning.write_current(
                                    db, item["subject_public_key"]
                                )
                                item["previous_write_assignment_digest"] = (
                                    previous_write.digest if previous_write else None
                                )
                    if self.inventory is not None:
                        result["inventory"] = self.inventory(policy.ceiling.read_grants)
                    result = self.decorate_inbox(result, policy)
                elif action.action == "revoke":
                    record = db.execute(
                        "SELECT * FROM access_requests WHERE id=?", (action.request_id,)
                    ).fetchone()
                    decision = db.execute(
                        "SELECT version,status FROM access_decisions WHERE request_id=?",
                        (action.request_id,),
                    ).fetchone()
                    if (
                        not record
                        or record[3] != action.request_digest_sha256
                        or action.expected_version != 2
                        or decision != (2, "active")
                        or record[1:3] != (action.target_public_key, action.target_installation_id)
                    ):
                        raise UnauthenticatedError("request_changed")
                    activation = self.revoke_selection(
                        db, action, record, policy, parsed.subject_public_key, current
                    )
                    db.execute(
                        "UPDATE access_decisions SET version=3,status='revoked',reason=? "
                        "WHERE request_id=?",
                        (action.reason, record[0]),
                    )
                    result = dict(
                        schema="devgraph.access-operator-result.v2",
                        action_id=action.action_id,
                        request_id=record[0],
                        request_digest_sha256=record[3],
                        version=3,
                        status="revoked",
                        activation=activation,
                        authority_issued=False,
                    )
                else:
                    record = db.execute(
                        "SELECT * FROM access_requests WHERE id=?", (action.request_id,)
                    ).fetchone()
                    decision = db.execute(
                        "SELECT version FROM access_decisions WHERE request_id=?",
                        (action.request_id,),
                    ).fetchone()
                    if (
                        not record
                        or record[6] <= current
                        or action.expected_version != 1
                        or decision
                        or record[3] != action.request_digest_sha256
                    ):
                        raise UnauthenticatedError("request_changed")
                    if action.action in ("review", "approve"):
                        plans = self._selection(action.selection, record, policy, current)
                        if self.provisioning is not None:
                            plans["activation"] = self.provisioning.preview(
                                db,
                                action.selection,
                                record,
                                self.previous_assignments(action),
                                current,
                            )
                        selection = action.selection.model_dump(
                            mode="json", by_alias=True, exclude_defaults=True
                        )
                        selection_digest = hashlib.sha256(canonical_json(selection)).hexdigest()
                    else:
                        selection = None
                        selection_digest = None
                        plans = None
                    if action.action == "review":
                        result = dict(
                            schema="devgraph.access-operator-result.v1",
                            action_id=action.action_id,
                            request_id=record[0],
                            request_digest_sha256=record[3],
                            expected_version=1,
                            review_id=action.action_id,
                            selection=selection,
                            selection_digest_sha256=selection_digest,
                            plans=plans,
                            authority_issued=False,
                        )
                    else:
                        if action.action == "approve":
                            review = db.execute(
                                "SELECT owner,result FROM access_operator_results "
                                "WHERE action_id=?",
                                (action.review_id,),
                            ).fetchone()
                            approved = json.loads(review[1]) if review else {}
                            if (
                                not review
                                or review[0] != parsed.subject_public_key
                                or approved.get("request_id") != record[0]
                                or approved.get("selection_digest_sha256") != selection_digest
                                or "review_id" not in approved
                            ):
                                raise UnauthenticatedError("exact_review_required")
                            # Review must belong to this exact policy revision as well.
                            review_digest = db.execute(
                                "SELECT digest FROM access_operator_results WHERE action_id=?",
                                (action.review_id,),
                            ).fetchone()[0]
                            reviewed_action = action.wire()
                            reviewed_action.update(action="review", action_id=action.review_id)
                            reviewed_action.pop("review_id", None)
                            if (
                                review_digest
                                != hashlib.sha256(
                                    canonical_json(
                                        dict(
                                            action=reviewed_action,
                                            policy_revision=policy.revision,
                                            policy_digest_sha256=policy.digest,
                                        )
                                    )
                                ).hexdigest()
                            ):
                                raise UnauthenticatedError("exact_review_required")
                        state = (
                            "approved_pending_activation"
                            if action.action == "approve"
                            else "denied"
                        )
                        activation = None
                        if action.action == "approve" and self.provisioning is not None:
                            activation = self.provisioning.activate(
                                db,
                                action.selection,
                                record,
                                self.previous_assignments(action),
                                parsed.subject_public_key,
                                action.action_id,
                                current,
                            )
                            state = "active"
                        db.execute(
                            "INSERT INTO access_decisions VALUES(?,2,?,?,?,?,?,?)",
                            (
                                record[0],
                                state,
                                action.action_id,
                                parsed.subject_public_key,
                                canonical_json(selection).decode() if selection else None,
                                action.reason,
                                current,
                            ),
                        )
                        result = dict(
                            schema="devgraph.access-operator-result.v1",
                            action_id=action.action_id,
                            request_id=record[0],
                            version=2,
                            status=state,
                            authority_issued=False,
                        )
                        if activation is not None:
                            result.update(
                                schema="devgraph.access-operator-result.v2",
                                activation=activation,
                                request_digest_sha256=record[3],
                            )
                if action.action == "inbox":
                    while (
                        len(canonical_json(result)) > 262144 and len(result["inbox"]["items"]) > 1
                    ):
                        result["inbox"]["items"].pop()
                        result["inbox"]["next_cursor"] = result["inbox"]["items"][-1]["request_id"]
                if len(canonical_json(result)) > 262144:
                    raise ValueError("operator result capacity exceeded")
                db.execute(
                    "INSERT INTO access_operator_results VALUES(?,?,?,?)",
                    (
                        action.action_id,
                        parsed.subject_public_key,
                        digest,
                        canonical_json(result).decode(),
                    ),
                )
                db.execute(
                    "INSERT INTO access_operator_audit VALUES(?,?,?,?,?)",
                    (action.action_id, parsed.subject_public_key, action.action, digest, current),
                )
            db.execute(
                "UPDATE access_operator_challenges SET used=1 WHERE id=?", (parsed.challenge_id,)
            )
            db.execute("COMMIT")
        return result
