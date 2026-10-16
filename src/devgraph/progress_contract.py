"""Work v2 additions; v1 canonical request normalization stays pinned."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from devgraph.model.validation import validate_work_object_id
from devgraph.workflow_contract import Evidence, RequirementVerdict, Strict, WorkflowReview


class ProgressSet(Strict):
    progress: Literal["not_started", "in_progress", "done"]
    reason: str = Field(min_length=1, max_length=8192)
    record_id: str
    evidence: list[Evidence] = Field(default_factory=list, max_length=50)
    requirements: list[RequirementVerdict] = Field(default_factory=list, max_length=100)

    _id = field_validator("record_id")(validate_work_object_id)

    @model_validator(mode="after")
    def valid(self):
        if not self.reason.strip():
            raise ValueError("missing_progress_reason")
        WorkflowReview(
            record_id=self.record_id,
            phase="requirements",
            verdict="approved",
            summary=self.reason,
            evidence=self.evidence,
            requirements=self.requirements,
        )
        return self


class ProposalReject(Strict):
    decision_id: str
    reason: str = Field(min_length=1, max_length=8192)
    _id = field_validator("decision_id")(validate_work_object_id)

    @field_validator("reason")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("missing_disposition_reason")
        return value
