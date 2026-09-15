"""Scope enforcement and audit for guarded work-graph operations.

Deny-by-default: an operation category is authorized only when the
envelope carries exactly the scope the policy matrix requires for it.
The admin scope grants nothing implicitly — the matrix in
``devgraph.auth.scopes`` already encodes that, and nothing here
special-cases it. There is no trusted-localhost bypass: nothing here
inspects hostnames, IPs, or network interfaces.

Verification happens per call: every guarded operation receives the
opaque credential and verifies it through the ``CredentialVerifier``
seam, so local and remote callers share the same scoped authorization
contract. Nothing is cached ambiently.

Export operations gate through the same matrix: internal export
requires exactly the internal export scope, and redacted /
public-safe-summary exports require exactly the redacted export scope.
Insufficient scope is a safe denial, never a silent downgrade to a
lesser mode. Enforcement for the tool/skill categories is proven at
the :func:`require_scope` level only.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from devgraph.auth.context import AuthorityContext
from devgraph.auth.errors import ForbiddenError
from devgraph.auth.scopes import (
    CATEGORY_EXPORT_INTERNAL,
    CATEGORY_EXPORT_REDACTED,
    CATEGORY_READ,
    CATEGORY_WRITE,
    REQUIRED_SCOPE_BY_CATEGORY,
)
from devgraph.auth.verifier import CredentialVerifier
from devgraph.model.base import WorkObject, WorkStatus
from devgraph.model.initiative_observations import (
    InitiativeObservation,
    InitiativeObservationRepository,
)
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import ContentChanges, WorkObjectRepository
from devgraph.model.validation import (
    validate_page_limit,
    validate_version,
    validate_work_object_id,
)
from devgraph.model.work import Decision, Issue, Proposal, Task, Todo
from devgraph.monitoring import build_monitor_snapshot
from devgraph.policy.export import ExportMode, export_records
from devgraph.policy.redaction import redact_event
from devgraph.relationships import RelationshipGraph
from devgraph.storage.base import GraphStorage
from devgraph.supporting_material import SupportingMaterialReader
from devgraph.supporting_material_contract import SupportingMaterialEnvelope, WorkDocumentEnvelope


def require_scope(context: AuthorityContext, category: str) -> None:
    """Raise :class:`ForbiddenError` unless the context envelope carries
    exactly the scope required for ``category``. Unknown categories are
    caller bugs and raise :class:`ValueError`."""
    required_scope = REQUIRED_SCOPE_BY_CATEGORY.get(category)
    if required_scope is None:
        raise ValueError(f"unknown operation category: {category!r}")
    if not context.envelope.has_scope(required_scope):
        raise ForbiddenError(
            f"scope for category '{category}' not granted",
            correlation_id=context.correlation_id,
        )


@dataclass(frozen=True)
class AuditRecord:
    """Safe audit identifiers for one authorized operation.

    Never carries credential strings or envelope scope contents."""

    actor_id: str
    session_id: str
    correlation_id: str
    category: str
    operation: str
    safe_summary: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    """In-memory audit sink collecting :class:`AuditRecord` entries."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self._lock = RLock()
        self._transactions: ContextVar[tuple[list[AuditRecord], ...]] = ContextVar(
            f"devgraph_audit_transactions_{id(self)}",
            default=(),
        )

    def _publish(self, entries: list[AuditRecord]) -> None:
        with self._lock:
            self.records.extend(entries)

    def record(
        self,
        *,
        actor_id: str,
        session_id: str,
        correlation_id: str,
        category: str,
        operation: str,
        safe_summary: dict[str, Any] | None = None,
    ) -> AuditRecord:
        entry = AuditRecord(
            actor_id=actor_id,
            session_id=session_id,
            correlation_id=correlation_id,
            category=category,
            operation=operation,
            safe_summary=redact_event(safe_summary or {}),
        )
        stack = self._transactions.get()
        if stack:
            stack[-1].append(entry)
        else:
            self._publish([entry])
        return entry

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Publish this transaction's records atomically, or discard them."""
        stack = self._transactions.get()
        pending: list[AuditRecord] = []
        token = self._transactions.set((*stack, pending))
        try:
            yield
        finally:
            self._transactions.reset(token)
        if stack:
            stack[-1].extend(pending)
        else:
            self._publish(pending)


class WriteSession(Protocol):
    """Single-request, verifier-backed capability for one write mutation."""

    @property
    def authority_context(self) -> AuthorityContext: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def accept_proposal(self, proposal_id: str, decision: Decision | None) -> Proposal: ...

    def convert_accepted_proposal_to_issue(self, proposal_id: str, issue_id: str) -> Issue: ...

    def create_work_object(self, work_object: WorkObject) -> WorkObject: ...

    def create_initiative_observation(
        self, observation: InitiativeObservation
    ) -> InitiativeObservation: ...

    def update_work_object(
        self, work_object: WorkObject, *, expected_version: int
    ) -> WorkObject: ...

    def update_work_object_content(
        self,
        kind: str,
        work_object_id: str,
        *,
        expected_version: int,
        changes: ContentChanges,
    ) -> WorkObject: ...

    def archive_work_object(self, kind: str, work_object_id: str) -> WorkObject: ...

    def transition_work_object_status(
        self, kind: str, work_object_id: str, new_status: WorkStatus
    ) -> WorkObject: ...


class AuthorizedWorkGraph:
    """Scope-enforcing façade over real work-graph operations.

    Delegates reads to :class:`RelationshipGraph` and writes to
    :class:`ProposalLifecycle` without modifying either. Every method
    verifies the per-call credential, requires the operation category's
    scope, delegates, then records a safe audit entry. Denied calls
    never reach the delegate and never write an audit record.
    """

    def __init__(
        self,
        verifier: CredentialVerifier,
        *,
        relationships: RelationshipGraph,
        lifecycle: ProposalLifecycle,
        audience: str,
        audit_log: AuditLog,
        repository: WorkObjectRepository | None = None,
        initiative_observations: InitiativeObservationRepository | None = None,
        monitor_storage: GraphStorage | None = None,
    ) -> None:
        self._verifier = verifier
        self._relationships = relationships
        self._lifecycle = lifecycle
        self._audience = audience
        self._audit_log = audit_log
        self._repository = repository
        self._initiative_observations = initiative_observations
        self._monitor_storage = monitor_storage
        self.__write_sessions: WeakKeyDictionary[_GraphWriteSession, AuthorityContext] = (
            WeakKeyDictionary()
        )
        self.__write_sessions_lock = RLock()

    def children_of(self, credential: str | None, parent: Todo) -> list[Todo]:
        context = self._authorize(credential, CATEGORY_READ)
        children = self._relationships.children_of(parent)
        self._audit(context, CATEGORY_READ, "children_of")
        return children

    def blockers_for(self, credential: str | None, task: Task) -> list[Task]:
        context = self._authorize(credential, CATEGORY_READ)
        blockers = self._relationships.blockers_for(task)
        self._audit(context, CATEGORY_READ, "blockers_for")
        return blockers

    def work_relationships(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        relationship: str,
        *,
        after_resource=None,
        limit=50,
    ) -> list[Todo]:
        context = self._authorize(credential, CATEGORY_READ)
        work = self._require_repository().get_by_id(kind, work_id)
        result = self._relationships.work_page(
            work, relationship, after_resource=after_resource, limit=limit
        )
        self._audit(context, CATEGORY_READ, "work_relationships")
        return result

    def supporting_material(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        *,
        limit: int = 50,
        after: str | None = None,
    ) -> SupportingMaterialEnvelope:
        context = self._authorize(credential, CATEGORY_READ)
        result = self._supporting_reader().read(kind, work_id, limit=limit, after=after)
        self._audit(context, CATEGORY_READ, "supporting_material")
        return result


    def work_document(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        artifact_id: str,
    ) -> WorkDocumentEnvelope:
        context = self._authorize(credential, CATEGORY_READ)
        item = self._supporting_reader().attached_artifact(kind, work_id, artifact_id)
        from devgraph.documents import document_roots_from_env, read_artifact_document

        if item.resolution == "missing":
            payload = {
                "schema": "devgraph.work-document.v1",
                "artifact_id": artifact_id,
                "state": "missing",
                "message": "The attached artifact record is missing.",
                "media_type": None,
                "size_bytes": None,
                "content": None,
            }
        elif item.resolution != "available":
            payload = {
                "schema": "devgraph.work-document.v1",
                "artifact_id": artifact_id,
                "state": "unavailable",
                "message": "The artifact metadata is not readable.",
                "media_type": None,
                "size_bytes": None,
                "content": None,
            }
        else:
            payload = read_artifact_document(
                artifact_id, item.metadata or {}, document_roots_from_env()
            )
        result = WorkDocumentEnvelope.model_validate(payload)
        self._audit(context, CATEGORY_READ, "work_document")
        return result


    def _supporting_reader(self) -> SupportingMaterialReader:
        if self._monitor_storage is None:
            raise RuntimeError("supporting material storage is not configured")
        return SupportingMaterialReader(self._monitor_storage, self._require_repository())

    def get_work_object(
        self,
        credential: str | None,
        kind: str,
        work_object_id: str,
    ) -> WorkObject:
        context = self._authorize(credential, CATEGORY_READ)
        validate_work_object_id(work_object_id)
        work_object = self._require_repository().get_by_id(kind, work_object_id)
        self._audit(context, CATEGORY_READ, "get_work_object")
        return work_object

    def read_arenas(self, credential, *, arena_id=None, include_archived=False,
                    after_id=None, limit=50):
        context = self._authorize(credential, CATEGORY_READ)
        from devgraph.arenas import ArenaRepository

        if self._monitor_storage is None:
            raise RuntimeError("Arena storage is not configured")
        repository = ArenaRepository(self._monitor_storage)
        result = repository.get(arena_id) if arena_id is not None else repository.list(
            include_archived=include_archived, after_id=after_id, limit=limit)
        self._audit(context, CATEGORY_READ, "read_arenas")
        return result

    def arena_members(self, credential, arena_id, *, after_resource=None, limit=50):
        context = self._authorize(credential, CATEGORY_READ)
        from devgraph.arenas import ArenaRepository

        if self._monitor_storage is None:
            raise RuntimeError("Arena storage is not configured")
        result = ArenaRepository(self._monitor_storage).members(
            arena_id, after_resource=after_resource, limit=limit)
        self._audit(context, CATEGORY_READ, "arena_members")
        return result

    def arena_membership(self, credential, kind, work_id):
        context = self._authorize(credential, CATEGORY_READ)
        from devgraph.arenas import ArenaRepository

        if self._monitor_storage is None:
            raise RuntimeError("Arena storage is not configured")
        result = ArenaRepository(self._monitor_storage).membership(kind, work_id)
        self._audit(context, CATEGORY_READ, "arena_membership")
        return result

    def query_work_objects(
        self,
        credential: str | None,
        kind: str,
        *,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ) -> list[WorkObject]:
        context = self._authorize(credential, CATEGORY_READ)
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be boolean")
        if not isinstance(descending, bool):
            raise TypeError("descending must be boolean")
        if after_id is not None:
            validate_work_object_id(after_id)
        validate_page_limit(limit)
        work_objects = self._require_repository().query(
            kind,
            include_archived=include_archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        self._audit(context, CATEGORY_READ, "query_work_objects")
        return work_objects

    def authorize_write(self, credential: str | None) -> WriteSession:
        """Verify and scope one request before any idempotency lookup."""
        context = self._authorize(credential, CATEGORY_WRITE)
        session = _GraphWriteSession(self, _WRITE_SESSION_CONSTRUCTOR)
        with self.__write_sessions_lock:
            self.__write_sessions[session] = context
        return session

    def _validate_write_session(self, session: _GraphWriteSession) -> AuthorityContext:
        with self.__write_sessions_lock:
            try:
                return self.__write_sessions[session]
            except KeyError:
                raise ForbiddenError("invalid write session") from None

    def _consume_write_session(self, session: _GraphWriteSession) -> AuthorityContext:
        with self.__write_sessions_lock:
            try:
                return self.__write_sessions.pop(session)
            except KeyError:
                raise ForbiddenError("invalid write session") from None

    def accept_proposal(
        self,
        credential: str | None,
        proposal_id: str,
        decision: Decision | None,
    ) -> Proposal:
        return self.authorize_write(credential).accept_proposal(proposal_id, decision)

    def convert_accepted_proposal_to_issue(
        self,
        credential: str | None,
        proposal_id: str,
        issue_id: str,
    ) -> Issue:
        return self.authorize_write(credential).convert_accepted_proposal_to_issue(
            proposal_id, issue_id
        )

    def create_work_object(
        self,
        credential: str | None,
        work_object: WorkObject,
    ) -> WorkObject:
        return self.authorize_write(credential).create_work_object(work_object)

    def update_work_object(
        self,
        credential: str | None,
        work_object: WorkObject,
        *,
        expected_version: int,
    ) -> WorkObject:
        return self.authorize_write(credential).update_work_object(
            work_object, expected_version=expected_version
        )

    def update_work_object_content(
        self,
        credential: str | None,
        kind: str,
        work_object_id: str,
        *,
        expected_version: int,
        changes: ContentChanges,
    ) -> WorkObject:
        return self.authorize_write(credential).update_work_object_content(
            kind,
            work_object_id,
            expected_version=expected_version,
            changes=changes,
        )

    def archive_work_object(
        self,
        credential: str | None,
        kind: str,
        work_object_id: str,
    ) -> WorkObject:
        return self.authorize_write(credential).archive_work_object(kind, work_object_id)

    def transition_work_object_status(
        self,
        credential: str | None,
        kind: str,
        work_object_id: str,
        new_status: WorkStatus,
    ) -> WorkObject:
        return self.authorize_write(credential).transition_work_object_status(
            kind, work_object_id, new_status
        )

    def get_initiative_observation(
        self,
        credential: str | None,
        observation_id: str,
    ) -> InitiativeObservation:
        context = self._authorize(credential, CATEGORY_READ)
        observation = self._require_initiative_observations().get_by_id(observation_id)
        self._audit(context, CATEGORY_READ, "get_initiative_observation")
        return observation

    def query_initiative_observations(
        self,
        credential: str | None,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ) -> list[InitiativeObservation]:
        context = self._authorize(credential, CATEGORY_READ)
        observations = self._require_initiative_observations().query(
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        self._audit(context, CATEGORY_READ, "query_initiative_observations")
        return observations

    def create_initiative_observation(
        self,
        credential: str | None,
        observation: InitiativeObservation,
    ) -> InitiativeObservation:
        return self.authorize_write(credential).create_initiative_observation(observation)

    def monitor_snapshot(self, credential: str | None) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_READ)
        if self._monitor_storage is None:
            raise ValueError("monitor storage is not configured")
        snapshot = build_monitor_snapshot(self._monitor_storage)
        self._audit(context, CATEGORY_READ, "monitor_snapshot")
        return snapshot

    def export_internal(
        self,
        credential: str | None,
        records: Iterable[WorkObject],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_INTERNAL)
        result = export_records(records, ExportMode.INTERNAL)
        self._audit(context, CATEGORY_EXPORT_INTERNAL, "export_internal")
        return result

    def export_redacted(
        self,
        credential: str | None,
        records: Iterable[WorkObject],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_REDACTED)
        result = export_records(records, ExportMode.REDACTED)
        self._audit(context, CATEGORY_EXPORT_REDACTED, "export_redacted")
        return result

    def export_public_safe_summary(
        self,
        credential: str | None,
        records: Iterable[WorkObject],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_REDACTED)
        result = export_records(records, ExportMode.PUBLIC_SAFE_SUMMARY)
        self._audit(context, CATEGORY_EXPORT_REDACTED, "export_public_safe_summary")
        return result

    def export_internal_by_ids(
        self,
        credential: str | None,
        kind: str,
        work_object_ids: Iterable[str],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_INTERNAL)
        records = self._fetch_export_records(kind, work_object_ids)
        result = export_records(records, ExportMode.INTERNAL)
        self._audit(context, CATEGORY_EXPORT_INTERNAL, "export_internal_by_ids")
        return result

    def export_redacted_by_ids(
        self,
        credential: str | None,
        kind: str,
        work_object_ids: Iterable[str],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_REDACTED)
        records = self._fetch_export_records(kind, work_object_ids)
        result = export_records(records, ExportMode.REDACTED)
        self._audit(context, CATEGORY_EXPORT_REDACTED, "export_redacted_by_ids")
        return result

    def export_public_safe_summary_by_ids(
        self,
        credential: str | None,
        kind: str,
        work_object_ids: Iterable[str],
    ) -> dict[str, Any]:
        context = self._authorize(credential, CATEGORY_EXPORT_REDACTED)
        records = self._fetch_export_records(kind, work_object_ids)
        result = export_records(records, ExportMode.PUBLIC_SAFE_SUMMARY)
        self._audit(context, CATEGORY_EXPORT_REDACTED, "export_public_safe_summary_by_ids")
        return result

    def _fetch_export_records(self, kind: str, work_object_ids: Iterable[str]) -> list[WorkObject]:
        """Authorized record source for exports (Issue 16). Fetching is part
        of the export operation and is covered by the export category alone:
        an export-scoped credential needs no read scope. A missing id fails
        closed before any export output exists — never a partial export."""
        identifiers = tuple(work_object_ids)
        for work_object_id in identifiers:
            validate_work_object_id(work_object_id)
        repository = self._require_repository()
        return [repository.get_by_id(kind, work_object_id) for work_object_id in identifiers]

    def _require_repository(self) -> WorkObjectRepository:
        if self._repository is None:
            raise ValueError("work-object repository is not configured")
        return self._repository

    def _require_initiative_observations(self) -> InitiativeObservationRepository:
        if self._initiative_observations is None:
            raise ValueError("initiative observation repository is not configured")
        return self._initiative_observations

    def _authorize(self, credential: str | None, category: str) -> AuthorityContext:
        context = self._verifier.verify(credential, audience=self._audience)
        require_scope(context, category)
        return context

    def _audit(self, context: AuthorityContext, category: str, operation: str) -> None:
        self._audit_log.record(
            actor_id=context.actor_id,
            session_id=context.session_id,
            correlation_id=context.correlation_id,
            category=category,
            operation=operation,
        )


_WRITE_SESSION_CONSTRUCTOR = object()


class _GraphWriteSession:
    """Graph-registered, immutable, single-use :class:`WriteSession`."""

    __slots__ = ("__weakref__", "_graph", "__sealed")

    def __init__(
        self,
        graph: AuthorizedWorkGraph,
        constructor: object,
    ) -> None:
        if constructor is not _WRITE_SESSION_CONSTRUCTOR:
            raise TypeError("write sessions are created by AuthorizedWorkGraph")
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_GraphWriteSession__sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_GraphWriteSession__sealed", False):
            raise AttributeError("write sessions are immutable")
        object.__setattr__(self, name, value)

    @property
    def authority_context(self) -> AuthorityContext:
        return self._graph._validate_write_session(self)

    def transaction(self) -> AbstractContextManager[None]:
        self._graph._validate_write_session(self)
        return self._graph._audit_log.transaction()

    def _consume(self) -> AuthorityContext:
        return self._graph._consume_write_session(self)

    def accept_proposal(self, proposal_id: str, decision: Decision | None) -> Proposal:
        context = self._consume()
        accepted = self._graph._lifecycle.accept_proposal(proposal_id, decision)
        self._graph._audit(context, CATEGORY_WRITE, "accept_proposal")
        return accepted

    def convert_accepted_proposal_to_issue(self, proposal_id: str, issue_id: str) -> Issue:
        context = self._consume()
        issue = self._graph._lifecycle.convert_accepted_proposal_to_issue(proposal_id, issue_id)
        self._graph._audit(context, CATEGORY_WRITE, "convert_accepted_proposal_to_issue")
        return issue

    def create_work_object(self, work_object: WorkObject) -> WorkObject:
        context = self._consume()
        validate_work_object_id(work_object.id)
        validate_version(work_object.version)
        created = self._graph._require_repository().create(work_object)
        self._graph._audit(context, CATEGORY_WRITE, "create_work_object")
        return created

    def create_initiative_observation(
        self,
        observation: InitiativeObservation,
    ) -> InitiativeObservation:
        context = self._consume()
        created = self._graph._require_initiative_observations().create(observation)
        self._graph._audit(context, CATEGORY_WRITE, "create_initiative_observation")
        return created

    def update_work_object(
        self,
        work_object: WorkObject,
        *,
        expected_version: int,
    ) -> WorkObject:
        context = self._consume()
        validate_work_object_id(work_object.id)
        validate_version(work_object.version)
        validate_version(expected_version)
        updated = self._graph._require_repository().update(
            work_object, expected_version=expected_version
        )
        self._graph._audit(context, CATEGORY_WRITE, "update_work_object")
        return updated

    def update_work_object_content(
        self,
        kind: str,
        work_object_id: str,
        *,
        expected_version: int,
        changes: ContentChanges,
    ) -> WorkObject:
        context = self._consume()
        validate_work_object_id(work_object_id)
        validate_version(expected_version)
        updated = self._graph._require_repository().update_content(
            kind,
            work_object_id,
            expected_version=expected_version,
            changes=changes,
        )
        self._graph._audit(context, CATEGORY_WRITE, "update_work_object_content")
        return updated

    def archive_work_object(self, kind: str, work_object_id: str) -> WorkObject:
        context = self._consume()
        validate_work_object_id(work_object_id)
        archived = self._graph._require_repository().archive(kind, work_object_id)
        self._graph._audit(context, CATEGORY_WRITE, "archive_work_object")
        return archived

    def transition_work_object_status(
        self,
        kind: str,
        work_object_id: str,
        new_status: WorkStatus,
    ) -> WorkObject:
        context = self._consume()
        validate_work_object_id(work_object_id)
        transitioned = self._graph._require_repository().transition_status(
            kind, work_object_id, new_status
        )
        self._graph._audit(context, CATEGORY_WRITE, "transition_work_object_status")
        return transitioned
