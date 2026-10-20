"""Explicit validity drafts; v1..v4 retain their finite wire contracts."""

from __future__ import annotations

import hashlib
import json
from typing import ClassVar, Literal

from pydantic import Field, TypeAdapter, field_serializer, field_validator, model_validator

from devgraph.auth.access_catalog import CatalogOperatorPolicy
from devgraph.auth.access_operator import OperatorAction
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.grant_validity import GrantValidity
from devgraph.auth.monitor_read import AccessDraftV2, MonitorWorkGrant
from devgraph.auth.path_access import (
    PathAccessDraft,
    PathEnrollmentPolicy,
    PathGrantSelection,
    PathMemberPolicy,
    PathOperatorAction,
)
from devgraph.auth.path_scope import PathWriteAssignment, ReceiverPathWritePolicy
from devgraph.auth.write_provisioning import NativeRule, NativeWritePolicy, WriteAccessDraft


class ExplicitValidity:
    """Mixin exposes finite compatibility accessors without serializing old fields."""

    not_before: ClassVar[int] = property(lambda self: self.validity.not_before)
    not_after: ClassVar[int | None] = property(lambda self: self.validity.not_after)

    @property
    def validity(self) -> GrantValidity:
        return self.validity_

    @field_validator("validity_", mode="before", check_fields=False)
    @classmethod
    def parse_validity(cls, value):
        if isinstance(value, GrantValidity):
            return value
        return GrantValidity.parse(canonical_json(value))

    @field_serializer("validity_", check_fields=False)
    def wire_validity(self, value):
        return json.loads(value.to_json())


class StandingAccessDraft(ExplicitValidity, PathAccessDraft):
    schema_: Literal["devgraph.access-draft.v5"] = Field(alias="schema")
    not_before: ClassVar[int] = ExplicitValidity.not_before
    not_after: ClassVar[int | None] = ExplicitValidity.not_after
    validity_: GrantValidity = Field(alias="validity")

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
        PathEnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v3",
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


ReadAssignment = TypeAdapter(StandingAccessDraft | PathAccessDraft | AccessDraftV2)
WriteAssignment = TypeAdapter(StandingAccessDraft | PathAccessDraft | WriteAccessDraft)


class StandingMemberPolicy(PathMemberPolicy):
    schema_: Literal["devgraph.member-access-policy.v4"] = Field(alias="schema")
    ceiling: StandingAccessDraft
    assignments: tuple[StandingAccessDraft | PathAccessDraft | AccessDraftV2, ...] = Field(
        max_length=128
    )


class StandingOperatorPolicy(CatalogOperatorPolicy):
    schema_: Literal["devgraph.access-operator-policy.v4"] = Field(alias="schema")
    ceiling: StandingAccessDraft


class StandingGrantSelection(PathGrantSelection):
    read: StandingAccessDraft | PathAccessDraft | AccessDraftV2 | None = None
    write: StandingAccessDraft | PathAccessDraft | WriteAccessDraft | None = None


class StandingOperatorAction(PathOperatorAction):
    schema_: Literal["devgraph.access-operator-action.v6", "devgraph.access-operator-action.v7"] = (
        Field(alias="schema")
    )
    scoped_write_schema = "devgraph.access-operator-action.v7"
    selection: StandingGrantSelection | None = None


Actions = TypeAdapter(StandingOperatorAction | PathOperatorAction | OperatorAction)


class StandingNativeRule(ExplicitValidity, NativeRule):
    not_before: ClassVar[int] = ExplicitValidity.not_before
    not_after: ClassVar[int | None] = ExplicitValidity.not_after
    validity_: GrantValidity = Field(alias="validity")

    @model_validator(mode="after")
    def interval(self):
        if self.resource_match == "exact" and self.resource.endswith("/"):
            raise ValueError("invalid native rule resource")
        return self


class StandingNativePolicy(NativeWritePolicy):
    schema_: Literal["secs-devgraph-work-policy.v2"] = Field(alias="schema")
    schema_version: int = Field(strict=True, ge=2, le=2)
    rules: tuple[StandingNativeRule, ...] = Field(min_length=1, max_length=1000)

    @property
    def digest(self):
        return hashlib.sha256(b"secs-devgraph-work-policy.v2\0" + canonical_json(self)).hexdigest()


class StandingWriteAssignment(ExplicitValidity, PathWriteAssignment):
    not_before: ClassVar[int] = ExplicitValidity.not_before
    not_after: ClassVar[int | None] = ExplicitValidity.not_after
    validity_: GrantValidity = Field(alias="validity")

    @model_validator(mode="after")
    def bounded(self):
        if len({canonical_json(g) for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate grant")
        return self


class StandingReceiverPolicy(ReceiverPathWritePolicy):
    schema_: Literal["devgraph.receiver-write-policy.v3"] = Field(alias="schema")
    assignments: tuple[StandingWriteAssignment | PathWriteAssignment, ...] = Field(max_length=128)
