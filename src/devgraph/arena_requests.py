"""Closed Arena request v1, carried by the existing signed operation transport."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from devgraph.arena_contract import MAX_SAFE_INTEGER, ArenaModel, ArenaReference
from devgraph.auth.secs_issue_create import (
    SecSIssueCreateDenied,
    _canonical_json,
    _strict_json_object,
)

ARENA_REQUEST_SCHEMA = "devgraph.arena-request.v1"
ARENA_REQUEST_DOMAIN = b"devgraph.arena-request.v1\x00"
ARENA_OPERATIONS = ("create", "patch", "archive", "member.set")


class InvalidArenaRequest(ValueError):
    """Safe closed-contract denial; never includes request content."""


class _Envelope(ArenaModel):
    schema_name: Literal["devgraph.arena-request.v1"] = Field(alias="schema")
    operation: Literal["create", "patch", "archive", "member.set"]
    kind: Literal["Arena", "Initiative", "Task"]
    id: str
    expected_version: int | None = Field(ge=1, le=MAX_SAFE_INTEGER)
    payload: dict


class _Create(ArenaModel):
    id: str
    title: str = Field(min_length=1, max_length=4096)
    description: str = Field(default="", max_length=32768)


class _Patch(ArenaModel):
    title: str | None = Field(default=None, min_length=1, max_length=4096)
    description: str | None = Field(default=None, max_length=32768)

    @model_validator(mode="before")
    @classmethod
    def nonempty_nonnull(cls, value):
        if not isinstance(value, dict) or not value or any(v is None for v in value.values()):
            raise ValueError("invalid Arena patch")
        return value


class _Membership(ArenaModel):
    previous_arena: ArenaReference | None
    arena: ArenaReference | None


@dataclass(frozen=True, repr=False)
class ArenaRequest:
    canonical: bytes
    operation: str
    kind: str
    id: str
    expected_version: int | None
    resources: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: bytes) -> ArenaRequest:
        try:
            value = _strict_json_object(raw, maximum_bytes=131_072, reason="invalid_arena_request")
            envelope = _Envelope.model_validate(value, strict=True)
            if (envelope.operation == "create") != (envelope.expected_version is None):
                raise ValueError("version precondition")
            if (envelope.operation == "member.set") != (envelope.kind != "Arena"):
                raise ValueError("Arena operation kind")
            model = {
                "create": _Create,
                "patch": _Patch,
                "archive": ArenaModel,
                "member.set": _Membership,
            }[envelope.operation]
            payload = model.model_validate(envelope.payload, strict=True).model_dump(
                mode="json",
                exclude_unset=envelope.operation == "patch",
            )
            resources = {f"{envelope.kind}/{envelope.id}"}
            if envelope.operation == "create" and payload["id"] != envelope.id:
                raise ValueError("create identity mismatch")
            if envelope.operation == "member.set":
                if all(reference is None for reference in payload.values()):
                    raise ValueError("empty membership change")
                for reference in payload.values():
                    if reference is not None:
                        resources.add(f"Arena/{reference['id']}")
            materialized = envelope.model_dump(mode="json", by_alias=True)
            materialized["payload"] = payload
            canonical = _canonical_json(materialized)
            if len(canonical) > 65_536:
                raise ValueError("request too large")
        except (SecSIssueCreateDenied, ValidationError, ValueError, TypeError, KeyError):
            raise InvalidArenaRequest("invalid_arena_request") from None
        return cls(
            canonical,
            envelope.operation,
            envelope.kind,
            envelope.id,
            envelope.expected_version,
            tuple(sorted(resources)),
        )

    @property
    def authority_operation(self) -> str:
        return f"devgraph.arena.{self.operation}.v1"

    @property
    def digest(self) -> str:
        return hashlib.sha256(ARENA_REQUEST_DOMAIN + self.canonical).hexdigest()

    @property
    def payload(self) -> dict:
        return json.loads(self.canonical)["payload"]
