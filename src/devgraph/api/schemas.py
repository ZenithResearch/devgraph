"""Strict request and typed response envelopes for the thin API adapter."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devgraph.arena_contract import Arena
from devgraph.model.validation import (
    validate_priority,
    validate_version,
    validate_work_object_id,
    validate_work_object_id_collection,
)
from devgraph.supporting_material_contract import (
    SupportingMaterialEnvelope as SupportingMaterialEnvelope,
)
from devgraph.supporting_material_contract import (
    WorkDocumentEnvelope as WorkDocumentEnvelope,
)

WorkKind = Literal["Proposal", "Initiative", "Project", "Issue", "Task"]
WorkStatusValue = Literal["draft", "review", "accepted", "archived"]
InitiativeObservationSubjectKindValue = Literal[
    "github_repository", "github_organization"
]
InitiativeObservationClaimStatusValue = Literal[
    "unclaimed", "claimed", "amended", "rejected"
]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("id", "decision_id", "issue_id", check_fields=False)
    @classmethod
    def validate_identifier(cls, value: object) -> str:
        return validate_work_object_id(value)

    @field_validator("priority", mode="before", check_fields=False)
    @classmethod
    def validate_signed_priority(cls, value: object) -> int:
        return validate_priority(value)

    @field_validator("artifact_ids", "external_link_ids", check_fields=False)
    @classmethod
    def validate_reference_identifiers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(validate_work_object_id_collection(values, expected_type=list))


class AcceptProposalRequest(_StrictRequest):
    decision_id: str = Field(min_length=1)
    decision_title: str = Field(min_length=1)


class ConvertProposalRequest(_StrictRequest):
    issue_id: str = Field(min_length=1)


class CreateWorkObjectRequest(_StrictRequest):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    priority: int = 0
    artifact_ids: list[str] = Field(default_factory=list)
    external_link_ids: list[str] = Field(default_factory=list)


class UpdateWorkObjectRequest(_StrictRequest):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority: int | None = None
    artifact_ids: list[str] | None = None
    external_link_ids: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("patch fields cannot be null")
        return value

    def model_post_init(self, __context: Any) -> None:
        if not self.model_fields_set:
            raise ValueError("patch must contain at least one field")


class StatusTransitionRequest(_StrictRequest):
    status: WorkStatusValue


class CreateInitiativeObservationRequest(_StrictRequest):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    subject_kind: InitiativeObservationSubjectKindValue
    subject_url: str = Field(min_length=1)
    github_node_id: str = ""
    source_commit: str = ""
    title: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    desired_state: str = Field(min_length=1)
    evidence_urls: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    observed_by: str = Field(min_length=1)
    observation_signature: str = ""

    @field_validator("project_id")
    @classmethod
    def validate_project_identifier(cls, value: object) -> str:
        return validate_work_object_id(value)


class InitiativeObservationEnvelope(BaseModel):
    schema_version: str
    id: str
    project_id: str
    subject_kind: InitiativeObservationSubjectKindValue
    subject_url: str
    github_node_id: str
    source_commit: str
    title: str
    problem: str
    desired_state: str
    evidence_urls: list[str]
    confidence: float
    authorship: Literal["inferred"]
    claim_status: InitiativeObservationClaimStatusValue
    observed_by: str
    observation_signature: str
    observed_at: str


class InitiativeObservationListEnvelope(BaseModel):
    items: list[InitiativeObservationEnvelope]


class ExportByIdsRequest(_StrictRequest):
    kind: WorkKind
    ids: list[str]

    @field_validator("ids")
    @classmethod
    def validate_identifiers(cls, values: list[object]) -> list[str]:
        return [validate_work_object_id(value) for value in values]


class WorkObjectEnvelope(BaseModel):
    id: str
    kind: str
    title: str
    description: str
    status: str
    version: int
    priority: int
    artifact_ids: list[str]
    external_link_ids: list[str]


class MutationReceiptEnvelope(BaseModel):
    receipt_id: str
    operation: str
    subject_label: str
    subject_id: str
    receipt_status: str
    duplicate: bool
    correlation_id: str


class WorkObjectListEnvelope(BaseModel):
    items: list[WorkObjectEnvelope]


class MutationResultEnvelope(BaseModel):
    work: WorkObjectEnvelope | None
    receipt: MutationReceiptEnvelope


class ArenaMutationResultEnvelope(BaseModel):
    arena: Arena | None
    work: WorkObjectEnvelope | None
    receipt: MutationReceiptEnvelope


class InitiativeObservationMutationResultEnvelope(BaseModel):
    observation: InitiativeObservationEnvelope | None
    receipt: MutationReceiptEnvelope


class InternalExportResponse(BaseModel):
    mode: Literal["internal"]
    records: list[dict[str, Any]]


class RedactedExportResponse(BaseModel):
    mode: Literal["redacted"]
    records: list[dict[str, Any]]
    omitted_private: int


class PublicSafeSummaryExportResponse(BaseModel):
    mode: Literal["public_safe_summary"]
    total: int
    by_kind: dict[str, int]
    private_records: int


class MonitorStorageEnvelope(BaseModel):
    live: bool
    ready: bool
    detail: str


class MonitorActivityEnvelope(BaseModel):
    id: str
    type: Literal["work", "observation", "receipt", "arena"]
    label: str
    title: str
    status: str
    timestamp: str


class MonitorWorkProgressEnvelope(BaseModel):
    schema_version: Literal["devgraph.work-progress.v0"]
    basis: Literal["leaf_work", "child_tasks", "self_status"]
    completed: int = Field(ge=0)
    total: int = Field(ge=1)
    percent: int = Field(ge=0, le=100)
    status_counts: dict[WorkStatusValue, int]

    @model_validator(mode="after")
    def validate_projection(self) -> MonitorWorkProgressEnvelope:
        expected_statuses = {"draft", "review", "accepted", "archived"}
        if set(self.status_counts) != expected_statuses:
            raise ValueError("progress status counts must cover canonical Work statuses")
        if any(value < 0 for value in self.status_counts.values()):
            raise ValueError("progress status counts must be non-negative")
        if sum(self.status_counts.values()) != self.total:
            raise ValueError("progress status counts must equal total")
        terminal = self.status_counts["accepted"] + self.status_counts["archived"]
        if terminal != self.completed:
            raise ValueError("progress completed must equal terminal status count")
        expected_percent = (self.completed * 100 + self.total // 2) // self.total
        if self.percent != expected_percent:
            raise ValueError("progress percent must match completed and total")
        return self


class MonitorGraphNodeEnvelope(BaseModel):
    key: str
    id: str
    kind: str
    title: str
    status: str
    category: Literal["work", "observation", "receipt", "arena"]
    archived: bool
    progress: MonitorWorkProgressEnvelope | None = None
    version: str | None = None


class MonitorGraphEdgeEnvelope(BaseModel):
    source: str
    target: str
    relationship: str


class MonitorSelectionGapEnvelope(BaseModel):
    work_key: str
    relationship: Literal["DEPENDS_ON", "BLOCKS"]
    reason: str


class MonitorSelectionScopeEnvelope(BaseModel):
    schema_: Literal["devgraph.selection-scope.v1"] = Field(alias="schema")
    edge_scan: Literal["all_stored_edges"]
    consistency: Literal["assembled"]
    read_started_at: str
    read_finished_at: str
    unresolved: list[MonitorSelectionGapEnvelope]


class MonitorSnapshotEnvelope(BaseModel):
    generated_at: str
    storage: MonitorStorageEnvelope
    total_work: int
    active_initiatives: int
    observation_count: int
    receipt_count: int
    pending_receipts: int
    work_by_kind: dict[str, int]
    work_by_status: dict[str, int]
    observation_by_status: dict[str, int]
    outbox_by_status: dict[str, int]
    recent_activity: list[MonitorActivityEnvelope]
    graph_nodes: list[MonitorGraphNodeEnvelope] = Field(default_factory=list)
    graph_edges: list[MonitorGraphEdgeEnvelope] = Field(default_factory=list)
    selection_scope: MonitorSelectionScopeEnvelope | None = None


class ProblemDetail(BaseModel):
    """RFC 7807-compatible safe error envelope."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str = ""
    correlation_id: str | None = None


_IF_MATCH = re.compile(r'^"[1-9][0-9]*"$')


def parse_if_match(value: str) -> int:
    if _IF_MATCH.fullmatch(value) is None:
        raise ValueError("Invalid version precondition")
    try:
        return validate_version(int(value[1:-1]))
    except ValueError as exc:
        raise ValueError("Invalid version precondition") from exc
