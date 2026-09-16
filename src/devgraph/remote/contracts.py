"""Strict transport-agnostic remote request contracts."""

from __future__ import annotations

import json
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PydanticUserError, TypeAdapter, field_validator

from devgraph.model.validation import validate_work_object_id

RemoteOperation: TypeAlias = Literal[
    "create_issue",
    "get_issue",
    "list_issues",
    "transition_issue_to_review",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )


def _validate_bounded_text(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    encoded = value.encode("utf-8")
    if not minimum <= len(encoded) <= maximum:
        raise ValueError(f"{field_name} is outside its UTF-8 byte bound")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} contains an ASCII control character")
    return value


class RemoteCredentialValidationError(ValueError):
    """Fixed public diagnostic without raw input or Pydantic error context."""

    def __init__(self) -> None:
        super().__init__("Invalid remote credential.")

    def errors(self, **_options: object) -> list[dict[str, object]]:
        return [{
            "type": "remote_credential_invalid",
            "loc": ("credential",),
            "msg": "Invalid remote credential.",
        }]

    def json(self, *, indent: int | None = None, **_options: object) -> str:
        return json.dumps(self.errors(), indent=indent)


def _credential_from_input(value: object) -> str:
    if type(value) is dict and value.keys() == {"credential"}:
        credential = value["credential"]
        if type(credential) is str:
            try:
                return _validate_bounded_text(
                    credential, field_name="credential", minimum=1, maximum=4096,
                )
            except ValueError:
                pass
    # Raise outside the handler so the public error has no raw exception context.
    raise RemoteCredentialValidationError()


class RemoteIngressCredential:
    """Opaque, frozen credential holder with a deliberately narrow validation API.

    Constructor, ``model_validate`` and ``model_validate_json`` are supported;
    dumps and repr omit the credential. Generic Pydantic nesting/TypeAdapter is
    unsupported: its JSON decoder can retain raw input before a model validator
    runs. Validate this holder separately before creating an ordinary model.
    This is not a BaseModel and does not expose unchecked construct/copy helpers.
    """

    __slots__ = ("_credential",)

    def __init__(self, **data: object) -> None:
        object.__setattr__(self, "_credential", _credential_from_input(data))

    @property
    def credential(self) -> str:
        return self._credential

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("RemoteIngressCredential is frozen.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("RemoteIngressCredential is frozen.")

    def __repr__(self) -> str:
        return "RemoteIngressCredential()"

    def model_dump(self, **_options: object) -> dict[str, object]:
        return {}

    def model_dump_json(self, **_options: object) -> str:
        return "{}"

    @classmethod
    def model_validate(cls, value: object) -> RemoteIngressCredential:
        if type(value) is cls:
            return value
        return cls(credential=_credential_from_input(value))

    @classmethod
    def model_validate_json(cls, value: str | bytes | bytearray) -> RemoteIngressCredential:
        if type(value) not in (str, bytes, bytearray):
            raise RemoteCredentialValidationError()
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            return cls.model_validate(decoded)
        raise RemoteCredentialValidationError()

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: object, _handler: object) -> object:
        raise PydanticUserError(
            "RemoteIngressCredential requires its explicit validation API; "
            "generic Pydantic adapters and nesting are unsupported.",
            code=None,
        )


class _WorkIdArguments(_StrictFrozenModel):
    work_id: str

    @field_validator("work_id", mode="before")
    @classmethod
    def validate_work_id(cls, value: object) -> str:
        return validate_work_object_id(value)


class CreateIssueArguments(_WorkIdArguments):
    title: str
    idempotency_key: str

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value: object) -> str:
        return _validate_bounded_text(value, field_name="title", minimum=1, maximum=512)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def validate_idempotency_key(cls, value: object) -> str:
        return _validate_bounded_text(
            value,
            field_name="idempotency_key",
            minimum=1,
            maximum=256,
        )


class GetIssueArguments(_WorkIdArguments):
    pass


class ListIssuesArguments(_StrictFrozenModel):
    include_archived: bool = False


class TransitionIssueToReviewArguments(_WorkIdArguments):
    idempotency_key: str

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def validate_idempotency_key(cls, value: object) -> str:
        return _validate_bounded_text(
            value,
            field_name="idempotency_key",
            minimum=1,
            maximum=256,
        )


class CreateIssueEnvelope(_StrictFrozenModel):
    operation: Literal["create_issue"]
    arguments: CreateIssueArguments


class GetIssueEnvelope(_StrictFrozenModel):
    operation: Literal["get_issue"]
    arguments: GetIssueArguments


class ListIssuesEnvelope(_StrictFrozenModel):
    operation: Literal["list_issues"]
    arguments: ListIssuesArguments


class TransitionIssueToReviewEnvelope(_StrictFrozenModel):
    operation: Literal["transition_issue_to_review"]
    arguments: TransitionIssueToReviewArguments


RemoteEnvelope: TypeAlias = Annotated[
    CreateIssueEnvelope | GetIssueEnvelope | ListIssuesEnvelope | TransitionIssueToReviewEnvelope,
    Field(discriminator="operation"),
]
REMOTE_ENVELOPE_ADAPTER = TypeAdapter(RemoteEnvelope)
