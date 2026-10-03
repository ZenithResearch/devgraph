"""Versioned Todo workflows, independent of Work lifecycle and containment."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devgraph.model.validation import validate_work_object_id

WorkflowId = Literal["vibe-ceo.v1", "execution.v1"]
COLUMNS = (
    "backlog",
    "planning",
    "plan_approval",
    "ready",
    "in_progress",
    "review",
    "done",
    "waiting",
)
COLUMN_LABELS = dict(
    zip(
        COLUMNS,
        (
            "Backlog",
            "Planning",
            "Plan approval",
            "Ready",
            "In progress",
            "Review",
            "Done",
            "Waiting",
        ),
        strict=True,
    )
)
PARENTS = {
    "Project": ("Initiative",),
    "Issue": ("Initiative", "Project"),
    "Task": ("Initiative", "Project", "Issue"),
}
WORK_KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")
LAYERS = ("product", "architecture", "implementation", "e2e", "design", "ux")
# Each row is exact state, human label, board column. These are the UI's source of truth.
STAGES = {
    "vibe-ceo.v1": (
        ("backlog", "Backlog", "backlog"),
        ("planning", "Requirements, context and plan", "planning"),
        ("developer_plan_approval", "Developer plan approval", "plan_approval"),
        ("ceo_plan_approval", "CEO plan approval", "plan_approval"),
        ("ready", "Approved plan, ready for execution", "ready"),
        ("in_progress", "Coordinating execution", "in_progress"),
        ("ceo_delivery_approval", "CEO delivery approval", "review"),
        ("technical_review", "Technical review", "review"),
        ("handoff_verification", "Handoff verification", "review"),
        ("done", "Done", "done"),
        ("blocked_waiting_input", "Blocked, waiting for input", "waiting"),
    ),
    "execution.v1": (
        ("backlog", "Backlog", "backlog"),
        ("intake", "Intake", "planning"),
        ("requirements_normalized", "Requirements normalized", "planning"),
        ("baseline_captured", "Baseline captured", "ready"),
        ("implementing_commit", "Implementing commit", "in_progress"),
        ("commit_ready_for_review", "Commit ready for review", "review"),
        ("layer_review", "Layer review", "review"),
        ("reconciling_ledger", "Reconciling ledger", "review"),
        ("delivery_packet_ready", "Delivery packet ready", "review"),
        ("done", "Done", "done"),
        ("blocked_waiting_input", "Blocked, waiting for input", "waiting"),
    ),
}
FORWARD = {
    key: dict(zip([r[0] for r in rows[:-2]], [r[0] for r in rows[1:-1]], strict=True))
    for key, rows in STAGES.items()
}
RETURNS = {
    "vibe-ceo.v1": {
        "in_progress": "planning",
        "developer_plan_approval": "planning",
        "ceo_plan_approval": "planning",
        "ceo_delivery_approval": "in_progress",
        "technical_review": "in_progress",
        "handoff_verification": "in_progress",
    },
    "execution.v1": {
        "implementing_commit": "intake",
        "commit_ready_for_review": "implementing_commit",
        "layer_review": "implementing_commit",
        "reconciling_ledger": "implementing_commit",
        "delivery_packet_ready": "implementing_commit",
    },
}
ENTRY_REVIEWS = {
    "developer_plan_approval": ("requirements",),
    "ceo_plan_approval": ("developer_plan",),
    "ready": ("ceo_plan",),
    "requirements_normalized": ("requirements",),
    "baseline_captured": ("baseline",),
    "commit_ready_for_review": ("commit",),
    "reconciling_ledger": ("technical",),
    "technical_review": ("ceo_delivery",),
    "handoff_verification": ("technical",),
    "done": ("technical", "handoff"),
}
COMPLETION_GATES = (
    "ceo_delivery_approval",
    "delivery_packet_ready",
    "handoff_verification",
    "done",
)
PHASES = (
    "requirements",
    "baseline",
    "developer_plan",
    "ceo_plan",
    "commit",
    "ceo_delivery",
    "technical",
    "handoff",
    "resolution",
)
RECORD_KINDS = {
    phase: (
        "Decision"
        if phase in {"developer_plan", "ceo_plan", "ceo_delivery", "resolution"}
        else "Handoff"
        if phase == "handoff"
        else "ReviewPacket"
    )
    for phase in PHASES
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class WorkflowState(Strict):
    schema_version: Literal[1] = 1
    workflow_id: WorkflowId
    stage: str
    resume_stage: str | None = None
    blocking_reason: str = Field(default="", max_length=4096)
    reviews: dict[str, str] = Field(default_factory=dict, max_length=9)

    @model_validator(mode="after")
    def valid(self):
        states = {r[0] for r in STAGES[self.workflow_id]}
        if self.stage not in states or (
            self.resume_stage is not None
            and (self.resume_stage not in states - {"done", "blocked_waiting_input"})
        ):
            raise ValueError("invalid_workflow_stage")
        if (self.stage == "blocked_waiting_input") != (self.resume_stage is not None):
            raise ValueError("invalid_workflow_resume")
        if self.stage == "blocked_waiting_input" and not self.blocking_reason.strip():
            raise ValueError("missing_blocking_reason")
        for phase, record_id in self.reviews.items():
            if phase not in PHASES:
                raise ValueError("invalid_review_phase")
            validate_work_object_id(record_id)
        return self


def encode_state(state: WorkflowState) -> str:
    return json.dumps(state.model_dump(), sort_keys=True, separators=(",", ":"))


def decode_state(raw: str) -> WorkflowState:
    if not isinstance(raw, str) or len(raw.encode()) > 16384:
        raise ValueError("invalid_workflow_state")
    return WorkflowState.model_validate_json(raw, strict=True)


def default_workflow(kind: str) -> str:
    return "execution.v1" if kind in {"Issue", "Task"} else "vibe-ceo.v1"


def initial_state(kind: str) -> str:
    return encode_state(WorkflowState(workflow_id=default_workflow(kind), stage="backlog"))


class WorkflowAssign(Strict):
    workflow_id: WorkflowId
    stage: str = "backlog"
    reason: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_stage(self):
        if self.stage not in {r[0] for r in STAGES[self.workflow_id]}:
            raise ValueError("invalid_workflow_stage")
        return self


class WorkflowTransition(Strict):
    stage: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=4096)


def safe_evidence_url(url: str) -> bool:
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
        return False
    match = re.match(r"^https?://([^/?#]*)", url)
    if match is None:
        return False
    authority = match[1]
    if authority.startswith("["):
        end = authority.find("]")
        try:
            ipaddress.IPv6Address(authority[1:end])
        except ValueError:
            return False
        if "%" in authority[1:end]:
            return False
        host, port = authority[: end + 1], authority[end + 1 :]
    else:
        host = authority.split(":", 1)[0]
        port = authority[len(host) :]
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
            return False
    return bool(host) and (
        not port or bool(re.fullmatch(r":[0-9]{1,5}", port)) and int(port[1:]) <= 65535
    )


class Evidence(Strict):
    """An existing reference, or an explicitly created external evidence link."""

    kind: Literal["Artifact", "ExternalLink"]
    id: str
    title: str = Field(default="", max_length=4096)
    url: str = Field(default="", max_length=4096)

    _id = field_validator("id")(validate_work_object_id)

    @model_validator(mode="after")
    def safe_link(self):
        if self.url:
            if (
                self.kind != "ExternalLink"
                or not self.title.strip()
                or not safe_evidence_url(self.url)
            ):
                raise ValueError("invalid_evidence_link")
        elif self.title:
            raise ValueError("existing_evidence_is_reference_only")
        return self


class RequirementVerdict(Strict):
    id: str
    subject: str = Field(default="", max_length=280)
    title: str = Field(min_length=1, max_length=4096)
    outcome: Literal["met", "unmet", "excluded"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(default="", max_length=4096)

    _id = field_validator("id")(validate_work_object_id)

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value):
        if value:
            kind, identifier = value.split("/", 1)
            if kind not in (*WORK_KINDS, "Requirement", "AcceptanceCriterion"):
                raise ValueError("invalid_requirement_subject")
            validate_work_object_id(identifier)
        return value


class LayerVerdict(Strict):
    layer: Literal["product", "architecture", "implementation", "e2e", "design", "ux"]
    verdict: Literal["approved", "changes_requested", "not_applicable"]
    reason: str = Field(default="", max_length=4096)


class WorkflowReview(Strict):
    record_id: str
    phase: Literal[
        "requirements",
        "baseline",
        "developer_plan",
        "ceo_plan",
        "commit",
        "ceo_delivery",
        "technical",
        "handoff",
        "resolution",
    ]
    verdict: Literal["approved", "changes_requested", "not_applicable"]
    summary: str = Field(min_length=1, max_length=8192)
    evidence: list[Evidence] = Field(default_factory=list, max_length=50)
    requirements: list[RequirementVerdict] = Field(default_factory=list, max_length=100)
    layers: list[LayerVerdict] = Field(default_factory=list, max_length=6)

    _id = field_validator("record_id")(validate_work_object_id)

    @model_validator(mode="after")
    def unique(self):
        for values in (
            [x.id for x in self.evidence],
            [x.id for x in self.requirements],
            [x.layer for x in self.layers],
        ):
            if len(set(values)) != len(values):
                raise ValueError("duplicate_review_item")
        evidence = {x.id for x in self.evidence}
        for row in self.requirements:
            if (
                not set(row.evidence_ids) <= evidence
                or (row.outcome == "met" and not row.evidence_ids)
                or (row.outcome == "excluded" and not row.reason.strip())
            ):
                raise ValueError("invalid_requirement_evidence")
        if any(x.verdict == "not_applicable" and not x.reason.strip() for x in self.layers):
            raise ValueError("missing_review_exclusion_reason")
        return self


def catalog() -> dict:
    return {
        "schema": "devgraph.workflows.v1",
        "columns": COLUMN_LABELS,
        "defaults": {kind: default_workflow(kind) for kind in WORK_KINDS},
        "workflows": {
            key: {
                "stages": [
                    dict(
                        id=s,
                        label=label,
                        column=column,
                        entry_reviews=list(ENTRY_REVIEWS.get(s, ())),
                        requires_completion=s in COMPLETION_GATES,
                    )
                    for s, label, column in rows
                ],
                "forward": FORWARD[key],
                "returns": RETURNS[key],
            }
            for key, rows in STAGES.items()
        },
        "review_layers": list(LAYERS),
    }
