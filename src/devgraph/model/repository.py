"""Kind-aware work-object repository over the ``GraphStorage`` protocol.

Specified by Issue #16. This module owns generic node<->model rehydration
for the full ``devgraph.model.work`` class set, keyed on the persisted
``kind`` property. It performs no authorization: the authorized
façade (``devgraph.auth.enforcement``) composes this repository as an
unauthorized delegate, exactly like ``RelationshipGraph`` and
``ProposalLifecycle``.

Fail-closed rule: an unknown, missing, or label-mismatched ``kind``
raises a safe error carrying identifiers only — never a partially
rehydrated object. Errors never echo node content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from types import MappingProxyType

from devgraph.model.base import (
    GENERIC_STATUS_TRANSITIONS,
    WorkObject,
    WorkStatus,
    utc_now,
)
from devgraph.model.validation import (
    CANONICAL_WORK_KINDS,
    validate_canonical_work_object_properties,
    validate_page_limit,
    validate_priority,
    validate_version,
    validate_work_object_id,
    validate_work_object_id_collection,
)
from devgraph.model.work import (
    AcceptanceCriterion,
    Blocker,
    Decision,
    Handoff,
    Initiative,
    Issue,
    Milestone,
    Project,
    Proposal,
    Requirement,
    ReviewPacket,
    Task,
    Todo,
)
from devgraph.storage.base import GraphStorage, NodeRecord

WORK_OBJECT_TYPES: Mapping[str, type[WorkObject]] = MappingProxyType(
    {
        cls.__name__: cls
        for cls in (
            Todo,
            Proposal,
            Initiative,
            Project,
            Issue,
            Task,
            Requirement,
            AcceptanceCriterion,
            Blocker,
            Decision,
            Handoff,
            ReviewPacket,
            Milestone,
        )
    }
)
if tuple(WORK_OBJECT_TYPES) != CANONICAL_WORK_KINDS:
    raise RuntimeError("canonical work-kind registry drift")


class WorkObjectRepositoryError(ValueError):
    """Base for safe repository errors. Carries identifiers only."""


class UnknownWorkObjectKindError(WorkObjectRepositoryError):
    """Raised when a requested or persisted kind is not a known work class."""


class MissingWorkObjectError(WorkObjectRepositoryError):
    """Raised when a work object does not exist for the given kind and id."""


class WorkObjectAlreadyExistsError(WorkObjectRepositoryError):
    """Raised when creating a work object whose kind and id already exist."""


class InvalidStatusTransitionError(WorkObjectRepositoryError):
    """Raised when a status change is outside the generic transition table."""


@dataclass(frozen=True)
class _Unset:
    pass


UNSET = _Unset()


@dataclass(frozen=True)
class ContentChanges:
    """Typed, omission-aware content patch accepted by the repository."""

    title: str | _Unset = UNSET
    description: str | _Unset = UNSET
    priority: int | _Unset = UNSET
    artifact_ids: tuple[str, ...] | _Unset = UNSET
    external_link_ids: tuple[str, ...] | _Unset = UNSET

    def __post_init__(self) -> None:
        values = self.as_replace_kwargs()
        if not values:
            raise ValueError("content changes cannot be empty")
        for name in ("title", "description"):
            if name in values and not isinstance(values[name], str):
                raise TypeError(f"{name} must be a string")
        if "title" in values and values["title"] == "":
            raise ValueError("title cannot be empty")
        if "priority" in values and (
            not isinstance(values["priority"], int) or isinstance(values["priority"], bool)
        ):
            raise TypeError("priority must be an integer")
        for name in ("artifact_ids", "external_link_ids"):
            if name in values:
                identifiers = values[name]
                if not isinstance(identifiers, tuple) or not all(
                    isinstance(item, str) for item in identifiers
                ):
                    raise TypeError(f"{name} must be a tuple of strings")
                validate_work_object_id_collection(identifiers, expected_type=tuple)

    @classmethod
    def from_mapping(cls, changes: Mapping[str, object]) -> ContentChanges:
        allowed = {field.name for field in fields(cls)}
        if not changes or not set(changes).issubset(allowed):
            raise ValueError("invalid content changes")
        values = dict(changes)
        for name in ("artifact_ids", "external_link_ids"):
            if name in values:
                value = values[name]
                if not isinstance(value, (list, tuple)):
                    raise TypeError(f"{name} must be a list of strings")
                values[name] = tuple(value)
        return cls(**values)

    def as_replace_kwargs(self) -> dict[str, object]:
        return {
            field.name: value
            for field in fields(self)
            if not isinstance((value := getattr(self, field.name)), _Unset)
        }


class WorkObjectVersionConflictError(WorkObjectRepositoryError):
    """Compare-and-set precondition failed. Carries identifiers and version
    numbers only — never node content."""

    def __init__(
        self, kind: str, work_object_id: str, *, expected_version: int, actual_version: int
    ) -> None:
        super().__init__(
            f"version conflict for {kind}: {work_object_id} "
            f"(expected {expected_version}, actual {actual_version})"
        )
        self.kind = kind
        self.work_object_id = work_object_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class WorkObjectRepository:
    """Generic work-object persistence over an injected ``GraphStorage``."""

    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    def get_by_id(self, kind: str, work_object_id: str) -> WorkObject:
        self._require_known_kind(kind)
        validate_work_object_id(work_object_id)
        node = self._storage.get_node(kind, work_object_id)
        if node is None:
            raise MissingWorkObjectError(f"missing {kind}: {work_object_id}")
        return self._from_node(node)

    def query(
        self,
        kind: str,
        *,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ) -> list[WorkObject]:
        self._require_known_kind(kind)
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be boolean")
        if not isinstance(descending, bool):
            raise TypeError("descending must be boolean")
        if after_id is not None:
            validate_work_object_id(after_id)
        validate_page_limit(limit)
        archived = None if include_archived else False
        nodes = self._storage.query(
            label=kind,
            archived=archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        return [self._from_node(node) for node in nodes]

    def create(self, work_object: WorkObject) -> WorkObject:
        self._require_known_kind(work_object.kind)
        validate_work_object_id(work_object.id)
        validate_version(work_object.version)
        validate_priority(work_object.priority)
        self._reject_creation_status_side_doors(work_object)
        with self._storage.transaction():
            if self._storage.get_node(work_object.kind, work_object.id) is not None:
                raise WorkObjectAlreadyExistsError(f"existing {work_object.kind}: {work_object.id}")
            try:
                self._storage.create_node(
                    work_object.kind, work_object.id, work_object.to_node_properties()
                )
            except KeyError:
                # GraphStorage reports a uniqueness loser as KeyError. Normalize
                # the check/create race to the repository's public conflict.
                raise WorkObjectAlreadyExistsError(
                    f"existing {work_object.kind}: {work_object.id}"
                ) from None
        return work_object

    def update(self, work_object: WorkObject, *, expected_version: int) -> WorkObject:
        """Compare-and-set content update. Rejects status changes: status
        moves only through ``transition_status``, ``archive``, or the
        provenance-bearing lifecycle operations."""
        validate_work_object_id(work_object.id)
        validate_version(work_object.version)
        validate_priority(work_object.priority)
        validate_version(expected_version)
        with self._storage.transaction():
            current = self.get_by_id(work_object.kind, work_object.id)
            if current.status == WorkStatus.ARCHIVED:
                raise InvalidStatusTransitionError(
                    f"cannot update archived {work_object.kind}: {work_object.id}"
                )
            if current.version != expected_version:
                raise WorkObjectVersionConflictError(
                    work_object.kind,
                    work_object.id,
                    expected_version=expected_version,
                    actual_version=current.version,
                )
            if work_object.status != current.status:
                raise InvalidStatusTransitionError(
                    f"update cannot change status for {work_object.kind}: {work_object.id}"
                )
            updated = replace(
                current,
                title=work_object.title,
                description=work_object.description,
                artifact_ids=work_object.artifact_ids,
                external_link_ids=work_object.external_link_ids,
                priority=work_object.priority,
                updated_at=utc_now(),
                version=current.version + 1,
            )
            self._storage.update_node(
                work_object.kind, work_object.id, updated.to_node_properties()
            )
        return updated

    def update_content(
        self,
        kind: str,
        work_object_id: str,
        *,
        expected_version: int,
        changes: ContentChanges,
    ) -> WorkObject:
        if not isinstance(changes, ContentChanges):
            raise TypeError("changes must be ContentChanges")
        self._require_known_kind(kind)
        validate_work_object_id(work_object_id)
        validate_version(expected_version)
        values = changes.as_replace_kwargs()
        if "priority" in values:
            validate_priority(values["priority"])
        with self._storage.transaction():
            node = self._storage.get_node(kind, work_object_id)
            if node is None:
                raise MissingWorkObjectError(f"missing {kind}: {work_object_id}")
            current = self._from_node(node)
            if current.status == WorkStatus.ARCHIVED:
                raise InvalidStatusTransitionError(
                    f"cannot update archived {kind}: {work_object_id}"
                )
            if current.version != expected_version:
                raise WorkObjectVersionConflictError(
                    kind,
                    work_object_id,
                    expected_version=expected_version,
                    actual_version=current.version,
                )
            updated = replace(
                current,
                **values,
                updated_at=utc_now(),
                version=current.version + 1,
            )
            self._storage.update_node(kind, work_object_id, updated.to_node_properties())
        return updated

    def archive(self, kind: str, work_object_id: str) -> WorkObject:
        self._require_known_kind(kind)
        validate_work_object_id(work_object_id)
        with self._storage.transaction():
            current = self.get_by_id(kind, work_object_id)
            if current.status == WorkStatus.ARCHIVED:
                raise InvalidStatusTransitionError(f"already archived {kind}: {work_object_id}")
            archived = current.with_status(WorkStatus.ARCHIVED)
            self._storage.archive_node(
                kind, work_object_id, archived.to_node_properties()
            )
        return archived

    def transition_status(
        self, kind: str, work_object_id: str, new_status: WorkStatus
    ) -> WorkObject:
        self._require_known_kind(kind)
        validate_work_object_id(work_object_id)
        with self._storage.transaction():
            current = self.get_by_id(kind, work_object_id)
            if (current.status, new_status) not in GENERIC_STATUS_TRANSITIONS:
                raise InvalidStatusTransitionError(
                    f"invalid transition {current.status.value} -> {new_status.value} "
                    f"for {kind}: {work_object_id}"
                )
            if new_status == WorkStatus.ACCEPTED and isinstance(current, Proposal):
                raise InvalidStatusTransitionError(
                    "Proposal acceptance requires Decision provenance via "
                    f"accept_proposal: {work_object_id}"
                )
            transitioned = current.with_status(new_status)
            self._storage.update_node(kind, work_object_id, transitioned.to_node_properties())
        return transitioned

    @staticmethod
    def _require_known_kind(kind: str) -> None:
        if kind not in WORK_OBJECT_TYPES:
            raise UnknownWorkObjectKindError(f"unknown work-object kind: {kind}")

    @staticmethod
    def _reject_creation_status_side_doors(work_object: WorkObject) -> None:
        if work_object.status == WorkStatus.ARCHIVED:
            raise InvalidStatusTransitionError(
                f"create cannot bypass archive semantics for {work_object.kind}: {work_object.id}"
            )
        if isinstance(work_object, Proposal) and work_object.status == WorkStatus.ACCEPTED:
            raise InvalidStatusTransitionError(
                "Proposal acceptance requires Decision provenance via "
                f"accept_proposal: {work_object.id}"
            )

    @staticmethod
    def _from_node(node: NodeRecord) -> WorkObject:
        persisted_kind = node.properties.get("kind")
        if not isinstance(persisted_kind, str) or persisted_kind not in WORK_OBJECT_TYPES:
            raise UnknownWorkObjectKindError(
                f"unknown persisted kind for node {node.label}:{node.id}"
            )
        if persisted_kind != node.label:
            raise UnknownWorkObjectKindError(
                f"kind/label mismatch for node {node.label}:{node.id}"
            )
        try:
            properties = validate_canonical_work_object_properties(
                node.label,
                node.id,
                node.properties,
                archived=node.archived,
                allow_unknown=True,
            )
            persisted_kind = properties["kind"]
            work_class = WORK_OBJECT_TYPES.get(persisted_kind)
            if work_class is None:
                raise ValueError("unknown persisted kind")
            return work_class(
                id=node.id,
                title=properties["title"],
                description=properties["description"],
                status=WorkStatus(properties["status"]),
                created_at=_parse_timestamp(properties["created_at"]),
                updated_at=_parse_timestamp(properties["updated_at"]),
                version=properties["version"],
                artifact_ids=tuple(properties["artifact_ids"]),
                external_link_ids=tuple(properties["external_link_ids"]),
                priority=properties["priority"],
            )
        except WorkObjectRepositoryError:
            raise
        except ValueError as error:
            if str(error) == "invalid_work_object_archive_state":
                raise WorkObjectRepositoryError(
                    f"archive status mismatch for node {node.label}:{node.id}"
                ) from error
            raise WorkObjectRepositoryError(
                f"unreadable properties for node {node.label}:{node.id}"
            ) from error
        except TypeError as error:
            raise WorkObjectRepositoryError(
                f"unreadable properties for node {node.label}:{node.id}"
            ) from error


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp property is not an ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp property must be timezone-aware")
    return parsed
