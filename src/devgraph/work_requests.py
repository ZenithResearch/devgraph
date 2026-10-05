"""Versioned, closed semantic requests for named Work mutations.

This is a request contract, not an authorization capability. Wallet and secS
must bind the complete canonical bytes; only a verified receiver may execute it.
The existing Issue-create v1 wire format is deliberately unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from devgraph.api.schemas import (
    AcceptProposalRequest,
    ConvertProposalRequest,
    CreateWorkObjectRequest,
    StatusTransitionRequest,
    UpdateWorkObjectRequest,
    WorkKind,
)
from devgraph.arena_contract import ArenaReference
from devgraph.auth.secs_issue_create import (
    SecSIssueCreateDenied,
    _canonical_json,
    _strict_json_object,
)
from devgraph.model.validation import validate_version, validate_work_object_id
from devgraph.progress_contract import ProgressSet, ProposalReject
from devgraph.workflow_contract import (
    PARENTS,
    RECORD_KINDS,
    WorkflowAssign,
    WorkflowReview,
    WorkflowTransition,
)

WORK_REQUEST_SCHEMA = "devgraph.work-request.v1"
WORK_REQUEST_DOMAIN = b"devgraph.work-request.v1\x00"
WORK_OPERATIONS = (
    "create",
    "patch",
    "status",
    "archive",
    "accept",
    "convert",
    "parent.set",
    "dependency.add",
    "dependency.remove",
    "blocker.add",
    "blocker.remove",
    "workflow.assign",
    "workflow.review",
    "workflow.transition",
)
WorkOperation = Literal[
    "create",
    "patch",
    "status",
    "archive",
    "accept",
    "convert",
    "parent.set",
    "dependency.add",
    "dependency.remove",
    "blocker.add",
    "blocker.remove",
    "workflow.assign",
    "workflow.review",
    "workflow.transition",
    "progress.set",
    "restore",
    "proposal.reject",
]


class InvalidWorkRequest(ValueError):
    """Safe request-contract failure, without request contents."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class WorkReference(_Strict):
    kind: WorkKind
    id: str
    expected_version: int

    @field_validator("id")
    @classmethod
    def canonical_id(cls, value: str) -> str:
        return validate_work_object_id(value)

    @field_validator("expected_version")
    @classmethod
    def positive_version(cls, value: int) -> int:
        return validate_version(value)


class _ParentChange(_Strict):
    previous_parent: WorkReference | None
    parent: WorkReference | None
    previous_arena: ArenaReference | None = None


class _EdgeChange(_Strict):
    target: WorkReference


class _Conversion(ConvertProposalRequest):
    decision_id: str

    @field_validator("decision_id")
    @classmethod
    def decision_is_canonical(cls, value: str) -> str:
        return validate_work_object_id(value)


class _Envelope(_Strict):
    schema_name: Literal["devgraph.work-request.v1", "devgraph.work-request.v2"] = Field(
        alias="schema"
    )
    operation: WorkOperation
    kind: Literal["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"]
    id: str
    expected_version: int | None
    payload: dict


WORK_OPERATIONS_V2 = tuple(op for op in WORK_OPERATIONS if op != "status") + (
    "progress.set",
    "restore",
    "proposal.reject",
)

_PAYLOAD_TYPES = {
    "progress.set": ProgressSet,
    "restore": _Strict,
    "proposal.reject": ProposalReject,
    "create": CreateWorkObjectRequest,
    "patch": UpdateWorkObjectRequest,
    "status": StatusTransitionRequest,
    "archive": _Strict,
    "accept": AcceptProposalRequest,
    "convert": _Conversion,
    "parent.set": _ParentChange,
    "dependency.add": _EdgeChange,
    "dependency.remove": _EdgeChange,
    "blocker.add": _EdgeChange,
    "blocker.remove": _EdgeChange,
    "workflow.assign": WorkflowAssign,
    "workflow.review": WorkflowReview,
    "workflow.transition": WorkflowTransition,
}
_PARENT_KIND = PARENTS


@dataclass(frozen=True, repr=False)
class WorkRequest:
    """Immutable canonical bytes plus derived routing and resource inventory."""

    canonical: bytes
    operation: str
    kind: str
    id: str
    expected_version: int | None
    resources: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: bytes) -> WorkRequest:
        try:
            value = _strict_json_object(raw, maximum_bytes=131_072, reason="invalid_work_request")
            envelope = _Envelope.model_validate(value, strict=True)
            legacy = envelope.schema_name == WORK_REQUEST_SCHEMA
            if legacy and (envelope.kind == "Todo" or envelope.operation not in WORK_OPERATIONS):
                raise ValueError("v2 operation requires v2 request")
            if not legacy and envelope.operation == "status":
                raise ValueError("legacy status is not canonical progress")
            if envelope.kind == "Todo" and envelope.operation not in {
                "create",
                "patch",
                "archive",
                "restore",
                "progress.set",
            }:
                raise ValueError("base Todo has no subtype lifecycle")
            validate_work_object_id(envelope.id)
            if envelope.operation == "create":
                if envelope.expected_version is not None:
                    raise ValueError("create precondition")
            else:
                validate_version(envelope.expected_version)
            payload_model = _PAYLOAD_TYPES[envelope.operation].model_validate(
                envelope.payload, strict=True
            )
            payload = payload_model.model_dump(
                mode="json", exclude_unset=envelope.operation in ("patch", "parent.set")
            )
            resources = {f"{envelope.kind}/{envelope.id}"}
            if envelope.operation == "create" and payload["id"] != envelope.id:
                raise ValueError("create subject mismatch")
            if envelope.operation in ("accept", "convert"):
                if envelope.kind != "Proposal":
                    raise ValueError("proposal operation")
                label, key = (
                    ("Decision", "decision_id")
                    if envelope.operation == "accept"
                    else ("Issue", "issue_id")
                )
                resources.add(f"{label}/{payload[key]}")
                if envelope.operation == "convert":
                    resources.add(f"Decision/{payload['decision_id']}")
            if envelope.operation == "parent.set":
                expected_kind = _PARENT_KIND.get(envelope.kind)
                if expected_kind is None or all(value is None for value in payload.values()):
                    raise ValueError("invalid parent change")
                for reference in (payload["previous_parent"], payload["parent"]):
                    if reference is not None:
                        if reference["kind"] not in expected_kind:
                            raise ValueError("invalid parent kind")
                        resources.add(f"{reference['kind']}/{reference['id']}")
                arena = payload.get("previous_arena")
                if arena is not None:
                    if envelope.kind != "Task" or payload["parent"] is None:
                        raise ValueError("invalid direct Arena parent change")
                    resources.add(f"Arena/{arena['id']}")
            if envelope.operation.startswith(("dependency.", "blocker.")):
                target = payload["target"]
                resource = f"{target['kind']}/{target['id']}"
                if resource in resources:
                    raise ValueError("self relationship")
                if envelope.operation.startswith("blocker.") and (
                    envelope.kind != "Task" or target["kind"] != "Task"
                ):
                    raise ValueError("blocker kinds")
                resources.add(resource)
            if envelope.operation == "workflow.review":
                resources.add(f"{RECORD_KINDS[payload['phase']]}/{payload['record_id']}")
                for evidence in payload["evidence"]:
                    if evidence["url"]:
                        resources.add(f"ExternalLink/{evidence['id']}")
            if envelope.operation == "progress.set":
                resources.add(f"ReviewPacket/{payload['record_id']}")
                for evidence in payload["evidence"]:
                    if evidence["url"]:
                        resources.add(f"ExternalLink/{evidence['id']}")
            if envelope.operation == "proposal.reject":
                if envelope.kind != "Proposal":
                    raise ValueError("proposal disposition requires Proposal")
                resources.add(f"Decision/{payload['decision_id']}")
            materialized = envelope.model_dump(mode="json", by_alias=True)
            materialized["payload"] = payload
            canonical = _canonical_json(materialized)
            if len(canonical) > 65_536:
                raise ValueError("request too large")
        except (SecSIssueCreateDenied, ValidationError, ValueError, TypeError, KeyError):
            raise InvalidWorkRequest("invalid_work_request") from None
        return cls(
            canonical=canonical,
            operation=envelope.operation,
            kind=envelope.kind,
            id=envelope.id,
            expected_version=envelope.expected_version,
            resources=tuple(sorted(resources)),
        )

    @property
    def version(self) -> int:
        return 2 if json.loads(self.canonical)["schema"] == "devgraph.work-request.v2" else 1

    @property
    def authority_operation(self) -> str:
        return f"devgraph.work.{self.operation}.v{self.version}"

    @property
    def digest(self) -> str:
        domain = f"devgraph.work-request.v{self.version}\x00".encode()
        return hashlib.sha256(domain + self.canonical).hexdigest()

    @property
    def payload(self) -> dict:
        # Return a new object so consumers cannot change the signed snapshot.
        return json.loads(self.canonical)["payload"]
