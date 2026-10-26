"""Additive Arena read activation over retained owner-reviewed assignments."""

import json

from devgraph.auth.arena_access import (
    ArenaAccessDraft,
    ArenaGrantSelection,
    ArenaMemberPolicy,
    ArenaOperatorAction,
    ReadAssignment,
    ReadGrants,
    WriteAssignment,
    compile_arena_read,
    permission_operation,
    read_operations,
)
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.owner_native_admission import OwnerGrantProvisioning
from devgraph.auth.path_activation import PathMonitorReadEnrollment
from devgraph.auth.standing_access import Actions as StandingActions
from devgraph.auth.standing_access import StandingAccessDraft
from devgraph.auth.standing_activation import StandingAccessOperator, StandingAccessRequestInbox


class ArenaAccessRequestInbox(StandingAccessRequestInbox):
    @staticmethod
    def decode_assignment(raw):
        return ReadAssignment.validate_json(raw)


class ArenaGrantProvisioning(OwnerGrantProvisioning):
    selection_type = ArenaGrantSelection
    registry_type = ArenaMemberPolicy
    registry_schema = "devgraph.member-access-policy.v5"

    @staticmethod
    def decode_draft(raw):
        return ReadAssignment.validate_json(raw)

    @staticmethod
    def decode_write_draft(raw):
        return WriteAssignment.validate_json(raw)

    @staticmethod
    def receiver_assignment(grant, owner):
        if isinstance(grant, ArenaAccessDraft):
            raw = json.loads(canonical_json(grant))
            raw.update(schema="devgraph.access-draft.v5", read_grants=[])
            grant = StandingAccessDraft.model_validate(raw)
        return OwnerGrantProvisioning.receiver_assignment(grant, owner)

    def preview(self, db, selection, record, previous_digest, now):
        value = super().preview(db, selection, record, previous_digest, now)
        drafts = [g for g in (selection.read, selection.write) if g is not None]
        if any(isinstance(g, ArenaAccessDraft) for g in drafts):
            from devgraph.auth.access_policy import _draft_validity
            from devgraph.auth.grant_validity import GrantValidity

            bounds = [_draft_validity(g) for g in drafts]
            end = min((g.not_after for g in bounds if g.not_after is not None), default=None)
            value["schema"] = value["schema"].removesuffix(".v1").removesuffix(".v2") + ".v2"
            value["validity"] = json.loads(
                GrantValidity(max(g.not_before for g in bounds), end).to_json()
            )
            if end is None:
                value.pop("expires_at", None)
        return value


class ArenaAccessOperator(StandingAccessOperator):
    write_action_schemas = (
        *StandingAccessOperator.write_action_schemas,
        "devgraph.access-operator-action.v9",
    )

    @staticmethod
    def parse_action(value):
        if value.get("schema") in (
            "devgraph.access-operator-action.v8",
            "devgraph.access-operator-action.v9",
        ):
            return ArenaOperatorAction.model_validate(value)
        return StandingActions.validate_python(value)

    @staticmethod
    def compile_read(draft):
        return compile_arena_read(draft)

    def decorate_inbox(self, result, policy):
        super().decorate_inbox(result, policy)
        result["activation"].update(
            schema="devgraph.access-activation-capabilities.v5",
            arena_reads=True,
            read_action_schema="devgraph.access-operator-action.v8",
            write_action_schema="devgraph.access-operator-action.v9",
        )
        return result


class ArenaMonitorReadEnrollment(PathMonitorReadEnrollment):
    def _policy_for(self, owner, device, current):
        return compile_arena_read(self._assignment(owner, device, current))

    def _details(self, policy):
        value = super()._details(policy)
        if policy.schema_ == "devgraph.native-read-policy.v4":
            value["schema"] = "devgraph.native-read-scope.v5"
        return value

    def _result(self, credential, manifest):
        value = super()._result(credential, manifest)
        if isinstance(
            self._assignment(
                manifest["subject_public_key"], manifest["installation_id"], manifest["issued_at"]
            ),
            ArenaAccessDraft,
        ):
            value["schema"] = "devgraph.native-read-result.v5"
        return value

    def _decode_enrollment_ceiling(self, grants):
        return tuple(ReadGrants.validate_python(g) for g in grants)

    def _decode_grants(self, permissions):
        from devgraph.auth.arena_access import VerifiedArenaReadGrant

        grants = self._decode_enrollment_ceiling([p["constraints"] for p in permissions])
        if any(
            p["operation"] not in {permission_operation(g, op) for op in read_operations(g)}
            for p, g in zip(permissions, grants, strict=True)
        ):
            raise ValueError("invalid read permissions")
        # A constraint can describe multiple operations, while a credential may
        # retain only one permission. Do not borrow list from a get permission.
        narrowed = [
            VerifiedArenaReadGrant(
                **g.model_dump(), operations=(p["operation"].removeprefix("devgraph.arena."),)
            )
            if g.scope == "devgraph.arena.read"
            else g.model_copy(
                update={"operations": (p["operation"].removeprefix("devgraph.work."),)}
            )
            for p, g in zip(permissions, grants, strict=True)
        ]
        return tuple({canonical_json(g): g for g in narrowed}.values())

    def _verified_grants(self, verified, credential):
        # secS has verified the signature and every permission against the current
        # policy before this hook runs. Members additionally need independently
        # granted Work list authority from the same signed credential, never the
        # server ceiling or another credential.
        return self._decode_grants(credential["claims"]["permissions"])
