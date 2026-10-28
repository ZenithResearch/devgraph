"""Opt-in private session adapter over ordinary issuer-owned read assignments.

No public routes or default runtime activation. The calling broker must be an
authenticated local application; client IDs are correlation, not authentication.
Owner consent and holder PoP remain mandatory native-compatible proofs. Existing
operator handlers, signed formats and stored grants are not rewritten.
"""

from __future__ import annotations

import json
import re
import secrets

from devgraph.auth.access_approval import WalletAccessChallenge, verify_wallet_approval
from devgraph.auth.access_operator import GrantSelection
from devgraph.auth.access_policy import require_subset
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.grant_validity import GrantValidity
from devgraph.auth.holder_grants import checksum
from devgraph.auth.native_read import _seconds
from devgraph.auth.session_read_profile import (
    SUPPORTED,
    build_read_session_selection,
    read_session_permissions,
)
from devgraph.session_review_contract import check_review


class SessionAccessProvider:
    """Private application API. Finite reads use lossless typed provider mappings."""

    def __init__(self, *, operator, holder, profile_digest, maximum_seconds=3600):
        if (
            holder.grant_schema != "secs.holder-grant.v2"
            or operator.provisioning is None
            or not re.fullmatch(r"[0-9a-f]{64}", profile_digest)
            or type(maximum_seconds) is not int
            or not 60 <= maximum_seconds <= 86400
        ):
            raise ValueError("unsupported_session_provider")
        self.operator, self.holder = operator, holder
        self.inbox = operator.inbox
        self.profile_digest, self.maximum_seconds = profile_digest, maximum_seconds
        self.selection_type = getattr(operator.provisioning, "selection_type", GrantSelection)
        if operator.policy().ceiling.schema_ not in SUPPORTED:
            raise ValueError("unsupported_session_policy_profile")
        if operator.policy().ceiling.issuer != holder.pin["issuer"]:
            raise ValueError("session_issuer_mismatch")
        with self.inbox._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS session_access_reviews (
                  handle TEXT PRIMARY KEY, client TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL,
                  review TEXT NOT NULL, selection TEXT NOT NULL, previous_digest TEXT,
                  state TEXT NOT NULL, enrollment TEXT, original_root TEXT);
                CREATE TABLE IF NOT EXISTS session_access_challenges (
                  id TEXT PRIMARY KEY, handle TEXT NOT NULL, payload TEXT NOT NULL,
                  used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS session_access_intents (
                  handle TEXT PRIMARY KEY, original TEXT NOT NULL);
            """)

    def _policy(self):
        self.operator._refresh_policy()
        return self.operator.policy()

    def _provider(self, policy):
        return dict(
            self.holder.pin,
            profile_digest_sha256=self.profile_digest,
            policy_binding=dict(
                policy_id="devgraph.session-access",
                policy_version=policy.revision,
                policy_digest_sha256=policy.digest,
            ),
        )

    def capabilities(self):
        return dict(
            schema="zenith.session-access-capabilities.v1",
            provider=self._provider(self._policy()),
            profile="secs.holder-grant.v2",
            session_key_enrollment=True,
            session_read_issuance=True,
            session_write_issuance=False,
            session_request_execution=True,
            expiry_limits=self._limits(),
            reason=None,
        )

    def _limits(self):
        return dict(
            minimum_seconds=60,
            default_seconds=min(3600, self.maximum_seconds),
            maximum_seconds=self.maximum_seconds,
        )

    def _permissions(self, draft):
        return read_session_permissions(draft, self.operator.compile_read, self.holder.pin)

    def _draft(self, policy, identity, permissions, expiry, current):
        return build_read_session_selection(
            ceiling=policy.ceiling,
            provisioning=self.operator.provisioning,
            compile_read=self.operator.compile_read,
            issuer_pin=self.holder.pin,
            identity=identity,
            permissions=permissions,
            not_before=current,
            expires_at=expiry,
        ).read

    def prepare(
        self,
        client,
        request_id,
        identity,
        permissions,
        expiry,
        display,
        *,
        now=None,
        lifetime_seconds=None,
    ):
        current, policy = _seconds(now), self._policy()
        if lifetime_seconds is not None and (
            expiry is not None
            or type(lifetime_seconds) is not int
            or not 60 <= lifetime_seconds <= self.maximum_seconds
        ):
            raise UnauthenticatedError("invalid_session_lifetime")
        intent = dict(
            holder=identity,
            permissions=permissions,
            display=display,
            timing=dict(lifetime_seconds=lifetime_seconds)
            if lifetime_seconds is not None
            else dict(expires_at=expiry),
        )
        if not isinstance(client, str) or not re.fullmatch(r"[0-9a-f]{32}", client):
            raise UnauthenticatedError("invalid_session_client")
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT client,review,handle,state FROM session_access_reviews WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous:
                if previous[0] != client:
                    raise UnauthenticatedError("session_client_mismatch")
                retained = json.loads(previous[1])
                original = db.execute(
                    "SELECT original FROM session_access_intents WHERE handle=?", (previous[2],)
                ).fetchone()
                if not original or original[0].encode() != canonical_json(intent):
                    raise UnauthenticatedError("session_request_conflict")
                if previous[3] != "awaiting_human":
                    raise UnauthenticatedError("session_original_outcome_required")
                return retained
            record = db.execute(
                "SELECT * FROM access_requests WHERE id=?", (request_id,)
            ).fetchone()
            if (
                not record
                or record[1:3] != (identity["public_key"], identity["installation_id"])
                or record[6] <= current
                or db.execute(
                    "SELECT 1 FROM access_decisions WHERE request_id=?", (request_id,)
                ).fetchone()
            ):
                raise UnauthenticatedError("authenticated_session_request_required")
            expiry = current + lifetime_seconds if lifetime_seconds is not None else expiry
            draft = self._draft(policy, identity, permissions, expiry, current)
            old = self.operator.provisioning.current(db, *record[1:3])
            previous_digest = old.digest if old else None
            if old is not None:
                raise UnauthenticatedError("new_session_holder_required")
            selection = self.selection_type(read=draft)
            self.operator.provisioning.preview(db, selection, record, previous_digest, current)
            handle = secrets.token_hex(16)
            manifest = dict(
                schema="zenith.session-access-review.v1",
                request_id=request_id,
                review_revision=1,
                provider=self._provider(policy),
                holder=identity,
                permissions=self._permissions(draft),
                not_before=current,
                expires_at=expiry,
                review_deadline=min(current + 120, expiry),
                challenge_id=secrets.token_hex(16),
                display=display,
            )
            review = dict(
                manifest=manifest,
                review_digest_sha256=checksum(manifest),
                request_handle=handle,
                state="awaiting_human",
            )
            check_review(
                review,
                provider=manifest["provider"],
                holder=identity,
                expiry_limits=self._limits(),
                now=current,
            )
            if db.execute("SELECT count(*) FROM session_access_reviews").fetchone()[0] >= 128:
                raise UnauthenticatedError("session_capacity")
            db.execute(
                "INSERT INTO session_access_reviews VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                (
                    handle,
                    client,
                    request_id,
                    canonical_json(review).decode(),
                    canonical_json(selection).decode(),
                    previous_digest,
                    "awaiting_human",
                ),
            )
            db.execute(
                "INSERT INTO session_access_intents VALUES(?,?)",
                (handle, canonical_json(intent).decode()),
            )
            db.execute("COMMIT")
            return review

    @staticmethod
    def _row(db, client, handle):
        row = db.execute(
            "SELECT * FROM session_access_reviews WHERE handle=?", (handle,)
        ).fetchone()
        if not row or row[1] != client:
            raise UnauthenticatedError("session_unavailable")
        return row

    def update_expiry(self, client, handle, expiry, *, now=None):
        current, policy = _seconds(now), self._policy()
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, client, handle)
            review, selection = json.loads(row[3]), self.selection_type.model_validate_json(row[4])
            manifest = review["manifest"]
            if row[6] != "awaiting_human" or manifest["provider"] != self._provider(policy):
                raise UnauthenticatedError("session_review_changed")
            raw = json.loads(canonical_json(selection.read))
            if "validity" in raw:
                raw["validity"] = json.loads(
                    GrantValidity.dated(selection.read.not_before, expiry).to_json()
                )
            else:
                raw["not_after"] = expiry
            # Decode with the configured profile; preserve all scope guards.
            draft = self.operator.provisioning.decode_draft(canonical_json(raw))
            require_subset(draft, policy.ceiling)
            manifest.update(
                expires_at=expiry,
                review_deadline=min(current + 120, expiry),
                review_revision=manifest["review_revision"] + 1,
                challenge_id=secrets.token_hex(16),
            )
            review["review_digest_sha256"] = checksum(manifest)
            check_review(
                review,
                provider=self._provider(policy),
                holder=manifest["holder"],
                expiry_limits=self._limits(),
                now=current,
            )
            db.execute(
                "UPDATE session_access_reviews SET review=?,selection=? WHERE handle=?",
                (
                    canonical_json(review).decode(),
                    canonical_json(self.selection_type(read=draft)).decode(),
                    handle,
                ),
            )
            db.execute("COMMIT")
            return review

    def owner_challenge(self, client, handle, owner, installation, *, now=None):
        current, policy = _seconds(now), self._policy()
        if owner not in policy.operator_public_keys:
            raise UnauthenticatedError("operator_required")
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, client, handle)
            review = json.loads(row[3])
            if row[6] != "awaiting_human":
                raise UnauthenticatedError("session_review_changed")
            check_review(
                review,
                provider=self._provider(policy),
                holder=review["manifest"]["holder"],
                expiry_limits=self._limits(),
                now=current,
            )
            value = dict(
                schema="castalia.access-request-challenge.v1",
                challenge_id=secrets.token_hex(16),
                nonce=secrets.token_urlsafe(32),
                issuer=self.inbox.issuer,
                receiver=self.inbox.receiver,
                subject_public_key=owner,
                installation_id=installation,
                operation="access.decide",
                issued_at=current,
                expires_at=current + 120,
                details=dict(
                    action=dict(
                        schema="devgraph.session-access-action.v1",
                        action="issue",
                        request_handle=handle,
                    ),
                    policy_revision=policy.revision,
                    policy_digest_sha256=policy.digest,
                    session_review={k: review[k] for k in ("manifest", "review_digest_sha256")},
                ),
            )
            WalletAccessChallenge.model_validate(value)
            if db.execute("SELECT count(*) FROM session_access_challenges").fetchone()[0] >= 256:
                raise UnauthenticatedError("session_challenge_capacity")
            db.execute(
                "INSERT INTO session_access_challenges VALUES(?,?,?,0)",
                (value["challenge_id"], handle, canonical_json(value).decode()),
            )
            db.execute("COMMIT")
            return value

    def submit(self, client, handle, challenge, signature, *, now=None):
        current, policy = _seconds(now), self._policy()
        parsed = verify_wallet_approval(
            challenge,
            signature,
            issuer=self.inbox.issuer,
            receiver=self.inbox.receiver,
            current=current,
        )
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, client, handle)
            review = json.loads(row[3])
            original = db.execute(
                "SELECT payload FROM session_access_challenges WHERE id=? AND handle=?",
                (parsed.challenge_id, handle),
            ).fetchone()
            if (
                not original
                or original[0].encode() != canonical_json(challenge)
                or parsed.subject_public_key not in policy.operator_public_keys
                or parsed.operation != "access.decide"
                or parsed.details
                != dict(
                    action=dict(
                        schema="devgraph.session-access-action.v1",
                        action="issue",
                        request_handle=handle,
                    ),
                    policy_revision=policy.revision,
                    policy_digest_sha256=policy.digest,
                    session_review={k: review[k] for k in ("manifest", "review_digest_sha256")},
                )
            ):
                raise UnauthenticatedError("session_approval_changed")
            if row[6] == "awaiting_human":
                check_review(
                    review,
                    provider=self._provider(policy),
                    holder=review["manifest"]["holder"],
                    expiry_limits=self._limits(),
                    now=current,
                )
                record = db.execute(
                    "SELECT * FROM access_requests WHERE id=?", (row[2],)
                ).fetchone()
                if db.execute(
                    "SELECT 1 FROM access_decisions WHERE request_id=?", (row[2],)
                ).fetchone():
                    raise UnauthenticatedError("session_request_changed")
                selection = self.selection_type.model_validate_json(row[4])
                require_subset(selection.read, policy.ceiling)
                self.operator.provisioning.activate(
                    db,
                    selection,
                    record,
                    row[5],
                    parsed.subject_public_key,
                    parsed.challenge_id,
                    current,
                )
                db.execute(
                    "INSERT INTO access_decisions VALUES(?,2,'active',?,?,?,?,?)",
                    (
                        record[0],
                        parsed.challenge_id,
                        parsed.subject_public_key,
                        row[4],
                        None,
                        current,
                    ),
                )
                db.execute(
                    "UPDATE session_access_reviews SET state='submitting' WHERE handle=?", (handle,)
                )
                db.execute(
                    "UPDATE session_access_challenges SET used=1 WHERE id=?", (parsed.challenge_id,)
                )
            db.execute("COMMIT")
        return self.reconcile(client, handle, now=now)

    def begin_enrollment(self, client, handle, *, now=None):
        with self.inbox._database() as db:
            row = self._row(db, client, handle)
            if row[6] != "submitting":
                raise UnauthenticatedError("session_not_approved")
            if row[7]:
                return json.loads(row[7])
            identity = json.loads(row[3])["manifest"]["holder"]
        challenge = self.holder.enrollment.challenge(
            identity["public_key"], identity["installation_id"], now=now
        )
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, client, handle)
            if row[7]:
                return json.loads(row[7])
            db.execute(
                "UPDATE session_access_reviews SET enrollment=? WHERE handle=?",
                (canonical_json(challenge).decode(), handle),
            )
            db.execute("COMMIT")
        return challenge

    def complete_enrollment(self, client, handle, challenge, signature, *, now=None):
        fields = dict(challenge=challenge, proof={}, signature=signature)
        original = dict(id=checksum(challenge)[:32], digest=checksum(fields))
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, client, handle)
            if (
                row[6] not in ("submitting", "issued")
                or not row[7]
                or row[7].encode() != canonical_json(challenge)
                or row[8]
                and row[8].encode() != canonical_json(original)
            ):
                raise UnauthenticatedError("session_enrollment_changed")
            db.execute(
                "UPDATE session_access_reviews SET original_root=? WHERE handle=?",
                (canonical_json(original).decode(), handle),
            )
            db.execute("COMMIT")
        self.holder.issue(**fields, now=now)
        return self.reconcile(client, handle, now=now)

    def _result(self, row, current, root=None):
        review = json.loads(row[3])
        manifest = review["manifest"]
        state = row[6]
        if current >= manifest["expires_at"]:
            state = "expired"
        elif row[8] and root is None and state not in ("revoked", "denied", "cancelled"):
            state = "outcome_unknown"
        return dict(
            schema="zenith.session-access-result.v1",
            request_handle=row[0],
            request_id=row[2],
            review_digest_sha256=review["review_digest_sha256"],
            provider_service=manifest["provider"]["service"],
            holder_public_key=manifest["holder"]["public_key"],
            state=state,
            credential_handle=("root_" + root["grant"]["claims"]["grant_id"])
            if state == "issued" and root
            else None,
            expires_at=manifest["expires_at"] if state == "issued" else None,
            reconcile_required=state == "outcome_unknown",
        )

    def reconcile(self, client, handle, *, now=None, internal_root=False):
        current = _seconds(now)
        with self.inbox._database() as db:
            row = self._row(db, client, handle)
        root = None
        if row[8] and current < json.loads(row[3])["manifest"]["expires_at"]:
            with self.holder.provider._database() as db:
                status = db.execute(
                    "SELECT status FROM holder_roots WHERE id=? AND service=?",
                    (json.loads(row[8])["id"], self.holder.service),
                ).fetchone()
            if status == ("revoked",):
                with self.inbox._database() as db:
                    db.execute(
                        "UPDATE session_access_reviews SET state='revoked' WHERE handle=?",
                        (handle,),
                    )
                    row = self._row(db, client, handle)
                return None if internal_root else self._result(row, current)
            with self.holder._provider_context():
                root = self.holder._recover(json.loads(row[8]), current)
            if root is not None:
                with self.inbox._database() as db:
                    db.execute(
                        "UPDATE session_access_reviews SET state='issued' WHERE handle=?", (handle,)
                    )
                    row = self._row(db, client, handle)
        return root if internal_root else self._result(row, current, root)

    def execute(self, client, handle, request_id, envelope, *, storage, audit_log):
        """Existing scoped read handler over local application IPC, not HTTP.

        Envelope is broker/helper internal; callers receive business data only.
        No model-selected credential/provider/receiver is trusted here.
        """
        from devgraph.api.signed_read import execute_signed_read
        from devgraph.api.wallet_monitor import wallet_monitor_read

        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise UnauthenticatedError("invalid_session_call")
        with self.inbox._database() as db:
            row = self._row(db, client, handle)
        if row[6] != "issued" or not row[8]:
            raise UnauthenticatedError("session_not_issued")
        original = json.loads(row[8])
        try:
            if envelope["credential"]["grant"]["claims"]["grant_id"] != original["id"]:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise UnauthenticatedError("session_credential_mismatch") from None
        parameters = envelope.get("call", {}).get("parameters")
        if isinstance(parameters, dict) and set(parameters) == {"path"}:
            result = wallet_monitor_read(storage, self.holder.enrollment, audit_log, envelope)
            if not isinstance(result, dict):
                raise UnauthenticatedError("session_read_failed")
        else:
            result = execute_signed_read(
                storage=storage,
                enrollment=self.holder.enrollment,
                audit_log=audit_log,
                body=envelope,
            ).model_dump(mode="json")
        value = dict(
            schema="zenith.session-call-result.v1",
            request_id=request_id,
            provider_service=self.holder.service,
            operation=envelope["call"]["operation"],
            result=result,
        )
        if len(canonical_json(value)) > 262144:
            raise UnauthenticatedError("session_result_capacity")
        return value
