"""Opt-in resource-path contracts. Legacy HTTP/runtime parsers remain closed.

The path is signed application-owned scope, not a native secS resource prefix.
Do not deploy these models through a legacy serializer that discards new fields.
Preset/catalog and matched runtime admission are a separate integration step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from devgraph.auth.monitor_read import MonitorWorkGrant
from devgraph.auth.write_scope import ReceiverWritePolicy, ScopedWriteGrant, WriteScopeAssignment
from devgraph.resource_paths import ResourcePath


class PathReadGrant(MonitorWorkGrant):
    schema_: Literal["devgraph.path-read-grant.v1"] = Field(alias="schema")
    resource_paths: tuple[ResourcePath, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_paths(self):
        if len(set(self.resource_paths)) != len(self.resource_paths):
            raise ValueError("duplicate resource path")
        return self


class PathWriteGrant(ScopedWriteGrant):
    schema_: Literal["devgraph.path-write-grant.v1"] = Field(alias="schema")
    resource_paths: tuple[ResourcePath, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def path_resources(self):
        if len(set(self.resource_paths)) != len(self.resource_paths):
            raise ValueError("duplicate resource path")
        if any(r.kind not in ("Initiative", "Project", "Issue", "Task") for r in self.resources):
            raise ValueError("only canonical hierarchical Work can use a path grant")
        return self


class PathWriteAssignment(WriteScopeAssignment):
    grants: tuple[PathWriteGrant | ScopedWriteGrant, ...] = Field(min_length=1, max_length=64)


class ReceiverPathWritePolicy(ReceiverWritePolicy):
    schema_: Literal["devgraph.receiver-write-policy.v2"] = Field(alias="schema")
    assignments: tuple[PathWriteAssignment, ...] = Field(max_length=128)
