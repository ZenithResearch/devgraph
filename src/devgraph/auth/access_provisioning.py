"""Explicit read-grant activation. No key, trust, policy file or native admin edits.

The private inbox is the authoritative overlay. A decision, active assignment and
receipt share one SQLite transaction. Existing file assignments remain readable;
replacing or masking one requires its exact digest in the signed v2 review.
"""

import hashlib

from devgraph.auth.access_policy import require_subset
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import (
    AccessDraftV2,
    MemberAccessPolicyV2,
    MemberMonitorReadEnrollment,
)


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


class ReadGrantProvisioning:
    def __init__(self, *, inbox, members):
        self.inbox, self.members = inbox, members
        inbox.provisioning = self
        with inbox._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS access_provisioning_head (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    base_revision INTEGER NOT NULL, base_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS access_assignments (
                    owner TEXT, device TEXT, request_id TEXT NOT NULL,
                    assignment TEXT NOT NULL, digest TEXT NOT NULL, active INTEGER NOT NULL,
                    PRIMARY KEY(owner,device));
                CREATE TABLE IF NOT EXISTS access_provisioning_audit (
                    action_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                    operation TEXT NOT NULL, assignment_digest TEXT NOT NULL,
                    actor TEXT NOT NULL, created INTEGER NOT NULL);
            """)
        self.refresh()

    def refresh(self):
        """Observe configuration monotonically, including on rejected calls."""
        base = self.members()
        if base.ceiling.issuer != self.inbox.issuer or base.ceiling.receiver != self.inbox.receiver:
            raise UnauthenticatedError("provisioning_receiver_mismatch")
        checksum = digest(base)
        with self.inbox._database() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT revision,base_revision,base_digest FROM access_provisioning_head WHERE id=1"
            ).fetchone()
            if old and (base.revision < old[1] or base.revision == old[1] and checksum != old[2]):
                raise UnauthenticatedError("provisioning_policy_rollback")
            if not old or base.revision > old[1]:
                # An observed narrowing permanently deactivates incompatible overlays;
                # a later expansion cannot resurrect credentials or old challenges.
                for owner, device, raw in db.execute(
                    "SELECT owner,device,assignment FROM access_assignments WHERE active=1"
                ).fetchall():
                    try:
                        require_subset(AccessDraftV2.model_validate_json(raw), base.ceiling)
                    except ValueError:
                        db.execute(
                            "UPDATE access_assignments SET active=0 WHERE owner=? AND device=?",
                            (owner, device),
                        )
                db.execute(
                    "INSERT OR REPLACE INTO access_provisioning_head VALUES(1,?,?,?)",
                    (
                        max(old[0] + 1, base.revision) if old else base.revision,
                        base.revision,
                        checksum,
                    ),
                )
            db.execute("COMMIT")
        return base

    def _base(self, db):
        base = self.members()
        row = db.execute(
            "SELECT base_revision,base_digest FROM access_provisioning_head WHERE id=1"
        ).fetchone()
        if row != (base.revision, digest(base)):
            raise UnauthenticatedError("provisioning_policy_changed")
        return base

    def registry(self):
        self.refresh()
        with self.inbox._database() as db:
            db.execute("BEGIN")
            base = self._base(db)
            revision = db.execute(
                "SELECT revision FROM access_provisioning_head WHERE id=1"
            ).fetchone()[0]
            assignments = {(g.subject_public_key, g.installation_id): g for g in base.assignments}
            for owner, device, raw, active in db.execute(
                "SELECT owner,device,assignment,active FROM access_assignments"
            ):
                assignments.pop((owner, device), None)
                if active:
                    grant = AccessDraftV2.model_validate_json(raw)
                    require_subset(grant, base.ceiling)
                    assignments[owner, device] = grant
            if len(assignments) > 128:
                raise UnauthenticatedError("provisioning_capacity")
            return MemberAccessPolicyV2.model_validate(
                dict(
                    schema="devgraph.member-access-policy.v2",
                    revision=revision,
                    ceiling=base.ceiling,
                    assignments=list(assignments.values()),
                )
            )

    def current(self, db, owner, device):
        base = self._base(db)
        row = db.execute(
            "SELECT assignment,active FROM access_assignments WHERE owner=? AND device=?",
            (owner, device),
        ).fetchone()
        if row:
            return AccessDraftV2.model_validate_json(row[0]) if row[1] else None
        return next(
            (
                g
                for g in base.assignments
                if (g.subject_public_key, g.installation_id) == (owner, device)
            ),
            None,
        )

    def preview(self, db, selection, record, previous_digest, now):
        if selection.write is not None:
            raise UnauthenticatedError("write_activation_unavailable")
        grant = selection.read
        if grant is None or grant.write_grants:
            raise UnauthenticatedError("read_activation_required")
        if (grant.subject_public_key, grant.installation_id) != record[1:3]:
            raise UnauthenticatedError("provisioning_binding_changed")
        base = self._base(db)
        require_subset(grant, base.ceiling)
        current = self.current(db, *record[1:3])
        if previous_digest != (current.digest if current else None):
            raise UnauthenticatedError("assignment_changed")
        if not grant.not_before <= now < grant.not_after:
            raise UnauthenticatedError("assignment_expired")
        # Count the union, not just active overlays. Replacements add no slot.
        keys = {(g.subject_public_key, g.installation_id) for g in base.assignments}
        keys.update(db.execute("SELECT owner,device FROM access_assignments").fetchall())
        if record[1:3] not in keys and len(keys) >= 128:
            raise UnauthenticatedError("provisioning_capacity")
        return dict(
            schema="devgraph.read-activation-plan.v1",
            assignment_digest=grant.digest,
            previous_assignment_digest=previous_digest,
            subject_public_key=record[1],
            installation_id=record[2],
            expires_at=grant.not_after,
            replaces_existing=current is not None,
        )

    def activate(self, db, selection, record, previous_digest, actor, action_id, now):
        plan = self.preview(db, selection, record, previous_digest, now)
        grant = selection.read
        db.execute(
            "INSERT OR REPLACE INTO access_assignments VALUES(?,?,?,?,?,1)",
            (*record[1:3], record[0], canonical_json(grant).decode(), grant.digest),
        )
        db.execute("UPDATE access_provisioning_head SET revision=revision+1 WHERE id=1")
        db.execute(
            "INSERT INTO access_provisioning_audit VALUES(?,?,?,?,?,?)",
            (action_id, record[0], "activate", grant.digest, actor, now),
        )
        return dict(plan, active=True, credential_issued=False)

    def revoke(self, db, record, previous_digest, actor, action_id, now):
        current = self.current(db, *record[1:3])
        row = db.execute(
            "SELECT request_id,digest,active FROM access_assignments WHERE owner=? AND device=?",
            record[1:3],
        ).fetchone()
        if (
            not current
            or not row
            or row != (record[0], previous_digest, 1)
            or current.digest != previous_digest
        ):
            raise UnauthenticatedError("assignment_changed")
        db.execute("UPDATE access_assignments SET active=0 WHERE owner=? AND device=?", record[1:3])
        db.execute("UPDATE access_provisioning_head SET revision=revision+1 WHERE id=1")
        db.execute(
            "INSERT INTO access_provisioning_audit VALUES(?,?,?,?,?,?)",
            (action_id, record[0], "revoke", current.digest, actor, now),
        )
        return dict(assignment_digest=current.digest, active=False, credential_issued=False)

    def summaries(self, db, policy, now):
        self._base(db)
        items = []
        rows = db.execute(
            "SELECT owner,device,request_id,assignment,digest FROM access_assignments "
            "WHERE active=1 ORDER BY request_id"
        ).fetchall()
        for owner, device, request_id, raw, checksum in rows:
            grant = AccessDraftV2.model_validate_json(raw)
            try:
                require_subset(grant, policy.ceiling)
            except ValueError:
                continue
            if not grant.not_before <= now < grant.not_after:
                continue
            record = db.execute(
                "SELECT digest FROM access_requests WHERE id=?", (request_id,)
            ).fetchone()
            if record is None:
                raise UnauthenticatedError("provisioning_state_invalid")
            items.append(
                dict(
                    request_id=request_id,
                    request_digest_sha256=record[0],
                    subject_public_key=owner,
                    installation_id=device,
                    assignment_digest=checksum,
                    expires_at=grant.not_after,
                    version=2,
                )
            )
        return items

    def check_snapshot(self, db, registry):
        # Attaching this retained private database takes a read lock for the rest
        # of the enrollment/read transaction. Concurrent revocation must wait,
        # rather than slip between the current-grant check and authorization.
        db.execute("ATTACH DATABASE ? AS provisioning", (str(self.inbox.path),))
        revision = db.execute(
            "SELECT revision FROM provisioning.access_provisioning_head WHERE id=1"
        ).fetchone()
        if revision != (registry.revision,):
            raise UnauthenticatedError("provisioning_policy_changed")


class ProvisionedMonitorReadEnrollment(MemberMonitorReadEnrollment):
    def __init__(self, *, provisioning, **kwargs):
        self.provisioning = provisioning
        super().__init__(members=provisioning.registry, **kwargs)

    def _check_policy_snapshot(self, db):
        super()._check_policy_snapshot(db)
        self.provisioning.check_snapshot(db, self._registry())
