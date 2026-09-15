"""Additive wire contract shared by the supporting-material producer and HTTP client."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devgraph.model.validation import (
    CANONICAL_WORK_STATUSES,
    SIGNED_64_MAX,
    validate_priority,
    validate_version,
    validate_work_object_id,
)

SupportingKind = Literal["Artifact", "ExternalLink", "Requirement", "AcceptanceCriterion"]
PublicWorkKind = Literal["Proposal", "Initiative", "Project", "Issue", "Task"]
METADATA_FIELDS = {
    "Artifact": {"title", "description", "role", "summary", "uri", "media_type", "archived"},
    "ExternalLink": {"title", "description", "role", "summary", "url", "external_id", "archived"},
    "Requirement": {"title", "description", "status", "priority", "version", "archived"},
    "AcceptanceCriterion": {"title", "description", "status", "priority", "version", "archived"},
}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class SupportingWorkIdentity(_Contract):
    kind: PublicWorkKind
    id: str
    version: int = Field(ge=1, le=SIGNED_64_MAX)

    _id = field_validator("id")(validate_work_object_id)


class SupportingMaterialVia(_Contract):
    field: Literal["artifact_ids", "external_link_ids"] | None = None
    relationships: list[str] = Field(default_factory=list, max_length=2)
    requirement_id: str | None = None

    @model_validator(mode="after")
    def valid_path(self):
        if self.field is not None:
            valid = not self.relationships and self.requirement_id is None
        elif self.requirement_id is not None:
            validate_work_object_id(self.requirement_id)
            valid = self.relationships == ["HAS_REQUIREMENT", "HAS_ACCEPTANCE_CRITERION"]
        else:
            valid = len(self.relationships) == 1 and self.relationships[0] in {
                "HAS_ARTIFACT",
                "HAS_EXTERNAL_LINK",
                "HAS_REQUIREMENT",
                "HAS_ACCEPTANCE_CRITERION",
            }
        if not valid:
            raise ValueError("invalid_supporting_provenance")
        return self


class SupportingMaterialItem(_Contract):
    kind: SupportingKind
    id: str
    via: list[SupportingMaterialVia] = Field(min_length=1, max_length=100)
    via_truncated: bool
    resolution: Literal["available", "missing", "malformed", "unsupported_profile"]
    metadata: dict[str, str | int | bool] | None
    issues: list[str] = Field(max_length=32)

    _id = field_validator("id")(validate_work_object_id)

    @model_validator(mode="after")
    def bounded_metadata(self):
        expected_edge = {
            "Artifact": "HAS_ARTIFACT",
            "ExternalLink": "HAS_EXTERNAL_LINK",
            "Requirement": "HAS_REQUIREMENT",
            "AcceptanceCriterion": "HAS_ACCEPTANCE_CRITERION",
        }[self.kind]
        expected_field = {"Artifact": "artifact_ids", "ExternalLink": "external_link_ids"}.get(
            self.kind
        )
        if any(
            (via.field is not None and via.field != expected_field)
            or (via.relationships and via.relationships[-1] != expected_edge)
            for via in self.via
        ):
            raise ValueError("invalid_supporting_provenance_kind")
        if any(len(issue) > 100 for issue in self.issues):
            raise ValueError("invalid_supporting_issue")
        if self.metadata is not None:
            if set(self.metadata) - METADATA_FIELDS[self.kind]:
                raise ValueError("invalid_supporting_metadata_fields")
            for key, value in self.metadata.items():
                expected = (
                    bool if key == "archived" else int if key in {"priority", "version"} else str
                )
                maximum = 65536 if key in {"description", "summary"} else 4096
                if type(value) is not expected or (isinstance(value, str) and len(value) > maximum):
                    raise ValueError("invalid_supporting_metadata_value")
                if key == "version":
                    validate_version(value)
                elif key == "priority":
                    validate_priority(value)
                elif key == "status" and value not in CANONICAL_WORK_STATUSES:
                    raise ValueError("invalid_supporting_status")
        if self.resolution == "available":
            required = {"title", "archived"}
            if self.kind in {"Requirement", "AcceptanceCriterion"}:
                required |= {"description", "status", "priority", "version"}
            if self.metadata is None or not required.issubset(self.metadata):
                raise ValueError("invalid_supporting_resolution")
        if self.resolution in {"missing", "unsupported_profile"} and self.metadata is not None:
            raise ValueError("invalid_supporting_resolution")
        return self


class SupportingMaterialEnvelope(_Contract):
    schema_: Literal["devgraph.work-supporting-material.v1"] = Field(alias="schema")
    work: SupportingWorkIdentity
    items: list[SupportingMaterialItem] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=4096)
    content_access: Literal["metadata_only"]

    @model_validator(mode="after")
    def unique_items(self):
        identities = [(item.kind, item.id) for item in self.items]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate_supporting_identity")
        return self


class WorkDocumentEnvelope(_Contract):
    schema_: Literal["devgraph.work-document.v1"] = Field(alias="schema")
    artifact_id: str
    state: Literal[
        "readable", "metadata_only", "unconfigured", "missing", "unsupported", "unavailable"
    ]
    message: str = Field(max_length=1000)
    media_type: str | None = Field(max_length=256)
    size_bytes: int | None = Field(ge=0, le=262144)
    content: str | None = Field(max_length=262144)

    _artifact_id = field_validator("artifact_id")(validate_work_object_id)

    @model_validator(mode="after")
    def readable_content(self):
        if self.state == "readable":
            if (
                self.content is None
                or self.media_type not in {"text/plain", "text/markdown"}
                or self.size_bytes != len(self.content.encode("utf-8"))
            ):
                raise ValueError("invalid_document_content")
        elif self.content is not None:
            raise ValueError("invalid_document_state")
        return self
