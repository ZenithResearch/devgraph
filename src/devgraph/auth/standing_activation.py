"""Opt-in standing-grant activation over the retained exact-decision registry."""

import json

from devgraph.auth.path_activation import (
    PathAccessOperator,
    PathAccessRequestInbox,
    PathGrantProvisioning,
)
from devgraph.auth.standing_access import (
    Actions,
    ReadAssignment,
    StandingGrantSelection,
    StandingMemberPolicy,
    StandingReceiverPolicy,
    WriteAssignment,
)


class StandingAccessRequestInbox(PathAccessRequestInbox):
    @staticmethod
    def decode_assignment(raw):
        return ReadAssignment.validate_json(raw)


class StandingGrantProvisioning(PathGrantProvisioning):
    selection_type = StandingGrantSelection
    registry_type = StandingMemberPolicy
    registry_schema = "devgraph.member-access-policy.v4"
    receiver_policy_type = StandingReceiverPolicy
    receiver_policy_schema = "devgraph.receiver-write-policy.v3"

    @staticmethod
    def decode_draft(raw):
        return ReadAssignment.validate_json(raw)

    @staticmethod
    def decode_write_draft(raw):
        return WriteAssignment.validate_json(raw)

    def preview(self, db, selection, record, previous_digest, now):
        value = super().preview(db, selection, record, previous_digest, now)
        drafts = [g for g in (selection.read, selection.write) if g is not None]
        if any(g.schema_ == "devgraph.access-draft.v5" for g in drafts):
            from devgraph.auth.access_policy import _draft_validity
            from devgraph.auth.grant_validity import GrantValidity

            bounds = [_draft_validity(g) for g in drafts]
            end = min((v.not_after for v in bounds if v.not_after is not None), default=None)
            validity = GrantValidity(max(v.not_before for v in bounds), end)
            value["schema"] = value["schema"].removesuffix(".v1") + ".v2"
            value["validity"] = json.loads(validity.to_json())
            if end is None:
                value.pop("expires_at", None)
        return value

    @staticmethod
    def receiver_assignment(grant, owner):
        from devgraph.auth.write_provisioning import WriteGrantProvisioning

        if grant.schema_ != "devgraph.access-draft.v5":
            return WriteGrantProvisioning.receiver_assignment(grant, owner)
        import hashlib

        return dict(
            actor_id="pubkey:sha256:" + hashlib.sha256(bytes.fromhex(owner)).hexdigest(),
            validity=json.loads(grant.validity.to_json()),
            grants=grant.write_grants,
        )


class StandingAccessOperator(PathAccessOperator):
    write_action_schemas = (
        *PathAccessOperator.write_action_schemas,
        "devgraph.access-operator-action.v7",
    )

    @staticmethod
    def parse_action(value):
        return Actions.validate_python(value)

    def decorate_inbox(self, result, policy):
        super().decorate_inbox(result, policy)
        result["activation"].update(
            schema="devgraph.access-activation-capabilities.v4",
            standing_validity=True,
            read_action_schema="devgraph.access-operator-action.v6",
            write_action_schema="devgraph.access-operator-action.v7",
        )
        return result
