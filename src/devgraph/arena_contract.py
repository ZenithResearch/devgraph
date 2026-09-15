"""Arena records and references, independent of the Work kind hierarchy."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from devgraph.model.validation import validate_work_object_id

MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ArenaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("id", "root_id", check_fields=False)
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_work_object_id(value)


class ArenaReference(ArenaModel):
    kind: Literal["Arena"]
    id: str
    expected_version: int = Field(ge=1, le=MAX_SAFE_INTEGER)


class Arena(ArenaModel):
    schema_version: Literal[1]
    kind: Literal["Arena"]
    id: str
    title: str = Field(min_length=1, max_length=4096)
    description: str = Field(max_length=32768)
    version: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    archived: bool
    created_at: str
    updated_at: str

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema(cls, value):
        if type(value) is not int:
            raise ValueError("invalid Arena schema version")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Arena timestamps must be timezone-aware")
        return value


class ArenaMembership(ArenaModel):
    arena: Arena | None
    root_kind: Literal["Proposal", "Initiative", "Project", "Issue", "Task"]
    root_id: str
    inherited: bool


class ArenaList(ArenaModel):
    items: tuple[Arena, ...]

    @field_validator("items", mode="before")
    @classmethod
    def freeze_items(cls, value):
        return tuple(value) if isinstance(value, list) else value
