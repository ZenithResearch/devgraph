"""Versioned Arena metadata reads; earlier Work-only contracts stay closed."""

from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from devgraph.auth.delegated_contract import ArenaReadGrant, canonical_json
from devgraph.auth.monitor_read import AccessDraftV2, MonitorWorkGrant
from devgraph.auth.path_access import PathAccessDraft, PathEnrollmentPolicy, PathReadGrant
from devgraph.auth.standing_access import (
    StandingAccessDraft,
    StandingGrantSelection,
    StandingMemberPolicy,
    StandingOperatorAction,
    StandingOperatorPolicy,
)
from devgraph.auth.write_provisioning import WriteAccessDraft

ArenaRead = ArenaReadGrant | PathReadGrant | MonitorWorkGrant


class VerifiedArenaReadGrant(ArenaReadGrant):
    """Internal verified permission, never an enrollment/policy wire format."""

    operations: tuple[Literal["list", "get"], ...]


def read_operations(grant):
    return getattr(grant, "operations", ("list", "get"))


def permission_operation(grant, operation):
    prefix = "devgraph.arena." if isinstance(grant, ArenaReadGrant) else "devgraph.work."
    return prefix + operation


class ArenaEnrollmentPolicy(PathEnrollmentPolicy):
    schema_: Literal["devgraph.native-read-policy.v4"] = Field(alias="schema")
    grants: tuple[ArenaRead, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_policy(self):
        work = tuple(g for g in self.grants if not isinstance(g, ArenaReadGrant))
        placeholder = MonitorWorkGrant(
            scope="devgraph.work.read",
            operations=("get",),
            relationship_types=("parent",),
            work_kinds=("Todo",),
            include_archived=False,
        )
        PathEnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v3",
                issuer=self.issuer,
                receiver=self.receiver,
                grants=work or (placeholder,),
                lifetime_seconds=self.lifetime_seconds,
            )
        )
        from devgraph.model.validation import validate_work_object_id

        for g in self.grants:
            if isinstance(g, ArenaReadGrant):
                if len(g.arena_ids) > 256:
                    raise ValueError("too many Arena identifiers")
                for identifier in g.arena_ids:
                    validate_work_object_id(identifier)
        if sum(len(read_operations(g)) for g in self.grants) > 32:
            raise ValueError("too many read permissions")
        if len({canonical_json(g) for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate grant")
        return self


class ArenaAccessDraft(StandingAccessDraft):
    schema_: Literal["devgraph.access-draft.v6"] = Field(alias="schema")
    read_grants: tuple[ArenaRead, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def valid(self):
        if not (self.read_grants or self.write_grants):
            raise ValueError("a nonempty grant is required")
        if self.installation_id is not None and self.subject_public_key is None:
            raise ValueError("installation binding requires a holder")
        placeholder = MonitorWorkGrant(
            scope="devgraph.work.read",
            operations=("get",),
            relationship_types=("parent",),
            work_kinds=("Todo",),
            include_archived=False,
        )
        ArenaEnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v4",
                issuer=self.issuer,
                receiver=self.receiver,
                grants=self.read_grants or (placeholder,),
                lifetime_seconds=self.lifetime_seconds,
            )
        )
        for grants in (self.read_grants, self.write_grants):
            if len({canonical_json(g) for g in grants}) != len(grants):
                raise ValueError("duplicate grant")
        return self


class ArenaMemberPolicy(StandingMemberPolicy):
    schema_: Literal["devgraph.member-access-policy.v5"] = Field(alias="schema")
    ceiling: ArenaAccessDraft
    assignments: tuple[
        ArenaAccessDraft | StandingAccessDraft | PathAccessDraft | AccessDraftV2, ...
    ] = Field(max_length=128)


class ArenaOperatorPolicy(StandingOperatorPolicy):
    schema_: Literal["devgraph.access-operator-policy.v5"] = Field(alias="schema")
    ceiling: ArenaAccessDraft


class ArenaGrantSelection(StandingGrantSelection):
    read: ArenaAccessDraft | StandingAccessDraft | PathAccessDraft | AccessDraftV2 | None = None
    write: ArenaAccessDraft | StandingAccessDraft | PathAccessDraft | WriteAccessDraft | None = None


class ArenaOperatorAction(StandingOperatorAction):
    schema_: Literal["devgraph.access-operator-action.v8", "devgraph.access-operator-action.v9"] = (
        Field(alias="schema")
    )
    scoped_write_schema = "devgraph.access-operator-action.v9"
    selection: ArenaGrantSelection | None = None

    @model_validator(mode="after")
    def read_precondition(self):
        if (
            self.schema_.endswith(".v8")
            and "previous_write_assignment_digest" in self.model_fields_set
        ):
            raise ValueError("read action cannot carry a write precondition")
        return self


ReadAssignment = TypeAdapter(
    ArenaAccessDraft | StandingAccessDraft | PathAccessDraft | AccessDraftV2
)
WriteAssignment = TypeAdapter(
    ArenaAccessDraft | StandingAccessDraft | PathAccessDraft | WriteAccessDraft
)
ReadGrants = TypeAdapter(ArenaRead)


def compile_arena_read(draft):
    if isinstance(draft, ArenaAccessDraft):
        return ArenaEnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v4",
                issuer=draft.issuer,
                receiver=draft.receiver,
                grants=draft.read_grants,
                lifetime_seconds=draft.lifetime_seconds,
            )
        )
    from devgraph.auth.path_access import compile_path_read

    return compile_path_read(draft)
