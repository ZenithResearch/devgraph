from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

from devgraph.model.validation import (
    validate_priority,
    validate_version,
    validate_work_object_id,
    validate_work_object_id_collection,
)

WorkObjectT = TypeVar("WorkObjectT", bound="WorkObject")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    ACCEPTED = "accepted"
    ARCHIVED = "archived"


TERMINAL_STATUSES = {WorkStatus.ACCEPTED, WorkStatus.ARCHIVED}

# Explicit table for the generic status-transition operation (Issue #16).
# ARCHIVED is never a generic target (the archive operation owns it),
# terminal statuses have no exits, and Proposal -> ACCEPTED is additionally
# excluded at the repository layer: acceptance requires Decision provenance
# and remains exclusively ProposalLifecycle.accept_proposal.
GENERIC_STATUS_TRANSITIONS: frozenset[tuple[WorkStatus, WorkStatus]] = frozenset(
    {
        (WorkStatus.DRAFT, WorkStatus.REVIEW),
        (WorkStatus.REVIEW, WorkStatus.DRAFT),
        (WorkStatus.DRAFT, WorkStatus.ACCEPTED),
        (WorkStatus.REVIEW, WorkStatus.ACCEPTED),
    }
)


@dataclass(frozen=True)
class WorkObject:
    id: str
    title: str
    description: str = ""
    status: WorkStatus = WorkStatus.DRAFT
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    version: int = 1
    artifact_ids: tuple[str, ...] = ()
    external_link_ids: tuple[str, ...] = ()
    priority: int = 0
    workflow_json: str | None = None
    progress: str | None = None
    archived: bool = False
    progress_record_id: str | None = None
    progress_migration_json: str | None = None

    def __post_init__(self) -> None:
        validate_work_object_id(self.id)
        validate_version(self.version)
        validate_priority(self.priority)
        validate_work_object_id_collection(self.artifact_ids, expected_type=tuple)
        validate_work_object_id_collection(self.external_link_ids, expected_type=tuple)
        for timestamp in (self.created_at, self.updated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")

    @property
    def kind(self) -> str:
        return type(self).__name__

    def to_node_properties(self) -> dict[str, Any]:
        properties = {
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "kind": self.kind,
            "artifact_ids": list(self.artifact_ids),
            "external_link_ids": list(self.external_link_ids),
            "priority": self.priority,
        }

        from devgraph.progress import TODO_KINDS

        if self.kind in TODO_KINDS:
            properties["progress_schema"] = 1
            if self.progress is not None:
                properties["progress"] = self.progress
            if self.progress_migration_json is not None:
                properties["progress_migration_json"] = self.progress_migration_json
            if self.progress_record_id is not None:
                properties["progress_record_id"] = self.progress_record_id
        if self.workflow_json is not None:
            properties["workflow_json"] = self.workflow_json
        return properties

    def with_status(self: WorkObjectT, status: WorkStatus) -> WorkObjectT:
        return replace(
            self,
            status=status,
            archived=True if status == WorkStatus.ARCHIVED else self.archived,
            updated_at=utc_now(),
            version=self.version + 1,
        )
