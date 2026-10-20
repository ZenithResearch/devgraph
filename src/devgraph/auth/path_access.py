"""Explicit v7 models; all earlier contracts keep their original decoders."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, model_validator

from devgraph.auth.access_operator import GrantSelection, OperatorAction
from devgraph.auth.access_policy import MemberAccessPolicy, _Closed
from devgraph.auth.access_requests import AccessIntent, Challenge, RequestAction
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.monitor_read import AccessDraftV2, EnrollmentPolicyV2, MonitorWorkGrant
from devgraph.auth.path_scope import PathReadGrant, PathWriteGrant
from devgraph.auth.write_provisioning import WriteAccessDraft
from devgraph.auth.write_scope import ScopedWriteGrant
from devgraph.resource_paths import ResourcePath
from devgraph.storage.delegated import validate_selectors


class PresetReference(_Closed):
    catalog_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: int = Field(strict=True, ge=1, le=9007199254740991)


class PathEnrollmentPolicy(EnrollmentPolicyV2):
    schema_: Literal["devgraph.native-read-policy.v3"] = Field(alias="schema")
    grants: tuple[PathReadGrant | MonitorWorkGrant, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_policy(self):
        url = urlsplit(self.receiver)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("receiver must be an HTTPS origin")
        if sum(len(g.operations) for g in self.grants) > 32:
            raise ValueError("too many read permissions")
        for g in self.grants:
            validate_selectors(g.work_ids, g.arena_ids)
        return self


class PathAccessDraft(WriteAccessDraft):
    schema_: Literal["devgraph.access-draft.v4"] = Field(alias="schema")
    read_grants: tuple[PathReadGrant | MonitorWorkGrant, ...] = Field(default=(), max_length=16)
    write_grants: tuple[PathWriteGrant | ScopedWriteGrant, ...] = Field(default=(), max_length=64)
    preset: PresetReference | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.not_before >= self.not_after or not (self.read_grants or self.write_grants):
            raise ValueError("a grant and valid interval are required")
        if self.installation_id is not None and self.subject_public_key is None:
            raise ValueError("installation binding requires a member")
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


ReadAssignment = TypeAdapter(PathAccessDraft | AccessDraftV2)
WriteAssignment = TypeAdapter(PathAccessDraft | WriteAccessDraft)


class PathMemberPolicy(MemberAccessPolicy):
    schema_: Literal["devgraph.member-access-policy.v3"] = Field(alias="schema")
    ceiling: PathAccessDraft
    assignments: tuple[PathAccessDraft | AccessDraftV2, ...] = Field(max_length=128)


class PathGrantSelection(GrantSelection):
    read: PathAccessDraft | AccessDraftV2 | None = None
    write: PathAccessDraft | WriteAccessDraft | None = None


class PathOperatorAction(OperatorAction):
    schema_: Literal["devgraph.access-operator-action.v4", "devgraph.access-operator-action.v5"] = (
        Field(alias="schema")
    )
    scoped_write_schema = "devgraph.access-operator-action.v5"
    selection: PathGrantSelection | None = None
    previous_write_assignment_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def mixed_previous(self):
        present = "previous_write_assignment_digest" in self.model_fields_set
        mixed = (
            self.selection and self.selection.read is not None and self.selection.write is not None
        )
        if self.action in ("review", "approve") and bool(mixed) != present:
            raise ValueError("mixed approval must bind both previous assignments")
        if present and self.action not in ("review", "approve", "revoke"):
            raise ValueError("unexpected write assignment precondition")
        if self.schema_.endswith(".v4") and present:
            raise ValueError("read action cannot carry a write precondition")
        return self

    def wire(self):
        value = super().wire()
        if "previous_write_assignment_digest" in self.model_fields_set:
            value["previous_write_assignment_digest"] = self.previous_write_assignment_digest
        return value


Actions = TypeAdapter(PathOperatorAction | OperatorAction)


class AccessPreference(PresetReference):
    id: str | None = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: int | None = Field(strict=True, ge=1, le=9007199254740991)
    resource_paths: tuple[ResourcePath, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def selected(self):
        if (self.id is None) != (self.version is None):
            raise ValueError("preset ID and version must be selected together")
        if len(set(self.resource_paths)) != len(self.resource_paths):
            raise ValueError("duplicate requested path")
        return self


class PresetIntent(AccessIntent):
    schema_: Literal["devgraph.access-intent.v2"] = Field(alias="schema")
    preference: AccessPreference

    @model_validator(mode="after")
    def nonempty(self):
        if self.read_grants or self.write_grants:
            raise ValueError("preset requests describe preferences; operator chooses exact grants")
        return self


class PresetRequestAction(RequestAction):
    schema_: Literal["devgraph.access-request-action.v2"] = Field(alias="schema")
    intent: PresetIntent | None


Requests = TypeAdapter(PresetRequestAction | RequestAction)


class PathRequestChallenge(Challenge):
    schema_: Literal[
        "devgraph.access-request-challenge.v1", "devgraph.access-request-challenge.v2"
    ] = Field(alias="schema")
    request: PresetRequestAction | RequestAction

    @model_validator(mode="after")
    def version(self):
        if self.schema_.endswith(".v2") != self.request.schema_.endswith(".v2"):
            raise ValueError("request/challenge version mismatch")
        return self


def compile_path_read(draft):
    if isinstance(draft, PathAccessDraft):
        return PathEnrollmentPolicy.model_validate(
            dict(
                schema="devgraph.native-read-policy.v3",
                issuer=draft.issuer,
                receiver=draft.receiver,
                grants=draft.read_grants,
                lifetime_seconds=draft.lifetime_seconds,
            )
        )
    from devgraph.auth.monitor_read import compile_monitor_read

    return compile_monitor_read(draft)
