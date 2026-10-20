"""Durable path grants and atomic read/write activation; no authority setup."""

import hashlib
import json

from devgraph.auth.access_catalog import catalog, scope_inventory, validate_preset
from devgraph.auth.access_operator import AccessOperator
from devgraph.auth.access_policy import _draft_validity, require_subset
from devgraph.auth.access_provisioning import (
    ProvisionedMonitorReadEnrollment,
    ReadGrantProvisioning,
)
from devgraph.auth.access_requests import AccessRequestInbox
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.monitor_read import MonitorWorkGrant
from devgraph.auth.path_access import (
    Actions,
    PathAccessDraft,
    PathGrantSelection,
    PathMemberPolicy,
    PathReadGrant,
    PathRequestChallenge,
    PresetIntent,
    ReadAssignment,
    Requests,
    WriteAssignment,
    compile_path_read,
)
from devgraph.auth.path_scope import ReceiverPathWritePolicy
from devgraph.auth.write_provisioning import WriteGrantProvisioning
from devgraph.storage.base import StorageUnavailable


def checksum(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


class PathAccessRequestInbox(AccessRequestInbox):
    challenge_type = PathRequestChallenge

    def __init__(self, *, catalog_policy, storage=None, **kwargs):
        self.catalog_policy, self.storage = catalog_policy, storage
        super().__init__(**kwargs)

    @staticmethod
    def parse_request(value):
        return Requests.validate_python(value)

    @staticmethod
    def challenge_schema(request):
        return (
            "devgraph.access-request-challenge.v2"
            if request.schema_.endswith(".v2")
            else "devgraph.access-request-challenge.v1"
        )

    @staticmethod
    def decode_assignment(raw):
        return ReadAssignment.validate_json(raw)

    def public_catalog(self):
        policy = self.catalog_policy()
        from devgraph.auth.native_read import _seconds

        if not _draft_validity(policy.ceiling).contains(_seconds(None)):
            raise UnauthenticatedError("operator_policy_expired")
        value = catalog(policy)
        value["scopes"] = (
            scope_inventory(
                self.storage, policy.ceiling.read_grants, published=policy.published_paths
            )
            if self.storage is not None
            else []
        )
        value["scope_semantics"] = "current_resources_at_access"
        return value

    def validate_intent(self, intent, current):
        if not isinstance(intent, PresetIntent):
            return super().validate_intent(intent, current)
        policy = self.catalog_policy()
        available, preference = catalog(policy), intent.preference
        if (
            not _draft_validity(policy.ceiling).contains(current)
            or preference.catalog_digest_sha256 != available["catalog_digest_sha256"]
        ):
            raise UnauthenticatedError("access_catalog_changed")
        if preference.id is not None and not any(
            p["id"] == preference.id and p["version"] == preference.version
            for p in available["presets"]
        ):
            raise ValueError("preset unavailable")
        if any(
            not any(
                selected.contained_by(p.resource_path)
                and (p.discover_descendants or selected == p.resource_path)
                for p in policy.published_paths
            )
            for selected in preference.resource_paths
        ):
            raise ValueError("requested path is not published")


class PathGrantProvisioning(WriteGrantProvisioning):
    registry_type = PathMemberPolicy
    registry_schema = "devgraph.member-access-policy.v3"
    receiver_policy_type = ReceiverPathWritePolicy
    receiver_policy_schema = "devgraph.receiver-write-policy.v2"
    mixed_enabled = True
    selection_type = PathGrantSelection

    @staticmethod
    def decode_draft(raw):
        return ReadAssignment.validate_json(raw)

    @staticmethod
    def decode_write_draft(raw):
        return WriteAssignment.validate_json(raw)

    def preview(self, db, selection, record, previous_digest, now):
        if selection.read is None or selection.write is None:
            return super().preview(db, selection, record, previous_digest, now)
        if not isinstance(previous_digest, dict) or set(previous_digest) != {"read", "write"}:
            raise UnauthenticatedError("both_assignments_required")
        read = ReadGrantProvisioning.preview(
            self, db, self.selection_type(read=selection.read), record, previous_digest["read"], now
        )
        write = WriteGrantProvisioning.preview(
            self,
            db,
            self.selection_type(write=selection.write),
            record,
            previous_digest["write"],
            now,
        )
        return dict(
            schema="devgraph.mixed-activation-plan.v1",
            assignment_digest=checksum(selection),
            previous_assignment_digest=previous_digest["read"],
            previous_write_assignment_digest=previous_digest["write"],
            read=read,
            write=write,
            expires_at=min(
                (v for v in (selection.read.not_after, selection.write.not_after) if v is not None),
                default=None,
            ),
        )

    def activate(self, db, selection, record, previous_digest, actor, action_id, now):
        if selection.read is None or selection.write is None:
            return super().activate(db, selection, record, previous_digest, actor, action_id, now)
        plan = self.preview(db, selection, record, previous_digest, now)
        read = ReadGrantProvisioning.activate(
            self,
            db,
            self.selection_type(read=selection.read),
            record,
            previous_digest["read"],
            actor,
            action_id + ":read",
            now,
        )
        write = WriteGrantProvisioning.activate(
            self,
            db,
            self.selection_type(write=selection.write),
            record,
            previous_digest["write"],
            actor,
            action_id + ":write",
            now,
        )
        return dict(
            plan,
            read=read,
            write=write,
            active=True,
            credential_issued=False,
            access_mode="read_write",
        )

    def summaries(self, db, policy, now):
        entries = super().summaries(db, policy, now)
        result = []
        for entry in entries:
            companion = next(
                (
                    r
                    for r in entries
                    if r["request_id"] == entry["request_id"] and r.get("access_mode") == "write"
                ),
                None,
            )
            if companion and entry.get("access_mode") != "write":
                result.append(
                    dict(
                        entry,
                        access_mode="read_write",
                        write_assignment_digest=companion["assignment_digest"],
                    )
                )
            elif entry.get("access_mode") != "write" or not any(
                r["request_id"] == entry["request_id"] and r.get("access_mode") != "write"
                for r in entries
            ):
                result.append(entry)
        return result

    def request_result(self, db, owner, request_id, now):
        value = super().request_result(db, owner, request_id, now)
        decision = db.execute(
            "SELECT selection FROM access_decisions WHERE request_id=?", (request_id,)
        ).fetchone()
        selected = json.loads(decision[0]) if decision and decision[0] else {}
        if not selected.get("read") or not selected.get("write"):
            return value
        active = {}
        for name, table in (("read", "access_assignments"), ("write", "write_assignments")):
            row = db.execute(
                "SELECT assignment,active,request_id FROM " + table + " WHERE owner=?", (owner,)
            ).fetchall()
            active[name] = any(
                enabled
                and original == request_id
                and (lambda g: _draft_validity(g).contains(now))(
                    self.decode_draft(raw) if name == "read" else self.decode_write_draft(raw)
                )
                for raw, enabled, original in row
            )
        state = (
            "active"
            if all(active.values())
            else "partially_active"
            if any(active.values())
            else "revoked"
        )
        return dict(
            schema="devgraph.access-request-result.v4",
            status=state,
            grant_active=all(active.values()),
            read_active=active["read"],
            write_active=active["write"],
        )


class PathMonitorReadEnrollment(ProvisionedMonitorReadEnrollment):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self._database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS path_enrollment_epochs ("
                "owner TEXT, device TEXT, epoch INTEGER NOT NULL, PRIMARY KEY(owner,device))"
            )

    def _prepare_enrollment(self, db, owner, device, policy):
        epoch = self._epoch(owner, device)
        row = db.execute(
            "SELECT epoch FROM path_enrollment_epochs WHERE owner=? AND device=?", (owner, device)
        ).fetchone()
        if row != (epoch,):
            # This runs only after real Wallet consent and the current approved
            # assignment checks, in the enrollment transaction. A new assignment
            # can widen permissions; renewal of the same assignment cannot.
            db.execute(
                "UPDATE enrollments SET ceiling=? WHERE owner=? AND device=? AND revoked=0",
                (
                    canonical_json(
                        [
                            g.model_dump(mode="json", by_alias=True, exclude_defaults=True)
                            for g in policy.grants
                        ]
                    ).decode(),
                    owner,
                    device,
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO path_enrollment_epochs VALUES(?,?,?)",
                (owner, device, epoch),
            )

    def _policy_for(self, owner, device, current):
        assignment = self._assignment(owner, device, current)
        if not assignment.read_grants:
            raise UnauthenticatedError("member_access_not_granted")
        return compile_path_read(assignment)

    def _details(self, policy):
        value = super()._details(policy)
        if policy.schema_ == "devgraph.native-read-policy.v3":
            value["schema"] = "devgraph.native-read-scope.v4"
        return value

    def _result(self, credential, manifest):
        value = super()._result(credential, manifest)
        if isinstance(
            self._assignment(
                manifest["subject_public_key"], manifest["installation_id"], manifest["issued_at"]
            ),
            PathAccessDraft,
        ):
            value["schema"] = "devgraph.native-read-result.v4"
        return value

    def _decode_enrollment_ceiling(self, grants):
        from pydantic import TypeAdapter

        return tuple(
            TypeAdapter(PathReadGrant | MonitorWorkGrant).validate_python(g) for g in grants
        )

    def _decode_grants(self, permissions):
        grants = self._decode_enrollment_ceiling([p["constraints"] for p in permissions])
        if any(
            p["operation"].removeprefix("devgraph.work.") not in g.operations
            for p, g in zip(permissions, grants, strict=True)
        ):
            raise ValueError("invalid read permissions")
        return tuple({canonical_json(g): g for g in grants}.values())


class PathAccessOperator(AccessOperator):
    write_action_schemas = (
        "devgraph.access-operator-action.v3",
        "devgraph.access-operator-action.v5",
    )

    def __init__(self, *, storage=None, **kwargs):
        self.storage = storage
        super().__init__(**kwargs)

    @staticmethod
    def parse_action(value):
        return Actions.validate_python(value)

    @staticmethod
    def compile_read(draft):
        return compile_path_read(draft)

    @staticmethod
    def previous_assignments(action):
        if (
            action.selection
            and action.selection.read is not None
            and action.selection.write is not None
        ):
            return dict(
                read=action.previous_assignment_digest,
                write=action.previous_write_assignment_digest,
            )
        return action.previous_assignment_digest

    def _selection(self, selection, record, policy, current):
        for draft in (selection.read, selection.write) if selection else ():
            if draft is not None:
                validate_preset(draft, policy)
        return super()._selection(selection, record, policy, current)

    def decorate_inbox(self, result, policy):
        result["catalog"] = catalog(policy)
        if self.storage is not None:
            try:
                result["scopes"] = scope_inventory(self.storage, policy.ceiling.read_grants)
            except StorageUnavailable:
                result["scope_inventory_unavailable"] = True
        result["activation"].update(
            schema="devgraph.access-activation-capabilities.v3",
            mixed=True,
            read_action_schema="devgraph.access-operator-action.v4",
            write_action_schema="devgraph.access-operator-action.v5",
            resource_paths=True,
        )
        return result

    def revoke_selection(self, db, action, record, policy, owner, current):
        if "previous_write_assignment_digest" not in action.model_fields_set:
            # A mixed decision has one lifecycle. Never mark it revoked while
            # leaving its other current component usable and unreachable.
            read = db.execute(
                "SELECT request_id,active FROM access_assignments WHERE owner=? AND device=?",
                record[1:3],
            ).fetchone()
            write = db.execute(
                "SELECT request_id,active FROM write_assignments WHERE owner=?", (record[1],)
            ).fetchone()
            if read == write == (record[0], 1):
                raise UnauthenticatedError("both_assignments_required")
            return super().revoke_selection(db, action, record, policy, owner, current)
        read = self.provisioning.current(db, *record[1:3])
        write = self.provisioning.write_current(db, record[1])
        if read is None or write is None:
            raise UnauthenticatedError("assignment_changed")
        require_subset(read, policy.ceiling)
        require_subset(write, policy.ceiling)
        r = self.provisioning.revoke(
            db,
            record,
            action.previous_assignment_digest,
            owner,
            action.action_id + ":read",
            current,
        )
        w = self.provisioning.revoke_write(
            db,
            record,
            action.previous_write_assignment_digest,
            owner,
            action.action_id + ":write",
            current,
        )
        return dict(
            assignment_digest=read.digest,
            write_assignment_digest=write.digest,
            active=False,
            credential_issued=False,
            read=r,
            write=w,
            access_mode="read_write",
        )
