"""Fail-closed service layer for the additive ``/devgraph`` read surface."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from devgraph.arenas import ArenaRepository
from devgraph.auth.context import AuthorityContext
from devgraph.auth.delegated_contract import (
    SCOPE_ARENA_READ,
    SCOPE_DOCUMENT_READ,
    SCOPE_GRAPH_READ,
    SCOPE_MATERIAL_READ,
    SCOPE_OBSERVATION_READ,
    SCOPE_QUERY_READ,
    SCOPE_WORK_READ,
    ArenaReadGrant,
    WorkReadGrant,
)
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import ForbiddenError, UnauthenticatedError
from devgraph.auth.verifier import CredentialVerifier
from devgraph.documents import document_roots_from_env, read_artifact_document
from devgraph.model.initiative_observations import InitiativeObservationRepository
from devgraph.model.repository import MissingWorkObjectError, WorkObjectRepository
from devgraph.monitoring import build_monitor_snapshot
from devgraph.storage.base import GraphStorage
from devgraph.storage.cypher_read import CypherReadService
from devgraph.storage.delegated import DelegatedWorkSelector
from devgraph.supporting_material import SupportingMaterialReader
from devgraph.supporting_material_contract import WorkDocumentEnvelope

_RELATIONSHIPS = {
    "children": ("HAS_CHILD", False),
    "parent": ("HAS_CHILD", True),
    "dependencies": ("DEPENDS_ON", False),
    "dependents": ("DEPENDS_ON", True),
    "blockers": ("BLOCKS", True),
    "blocked": ("BLOCKS", False),
}


class DelegatedReadService:
    """Authorize granular claims, then execute typed storage-backed reads."""

    def __init__(
        self,
        *,
        storage: GraphStorage,
        verifier: CredentialVerifier,
        audience: str,
        audit_log: AuditLog,
        cypher_read: CypherReadService | None = None,
    ) -> None:
        self._storage = storage
        self._verifier = verifier
        self._audience = audience
        self._audit_log = audit_log
        self._repository = WorkObjectRepository(storage)
        self._arenas = ArenaRepository(storage)
        self._observations = InitiativeObservationRepository(storage)
        self._supporting = SupportingMaterialReader(storage, self._repository)
        self._cypher_read = cypher_read

    def authorize(self, credential: str | None, scope: str) -> AuthorityContext:
        context = self._verifier.verify(credential, audience=self._audience)
        if (
            context.credential_id is None
            or context.lifecycle_status != "active"
            or context.resource != "https://work.zenith-research.ca/devgraph"
            or not context.read_grants
        ):
            raise UnauthenticatedError("invalid_credential")
        if scope not in context.scopes:
            raise ForbiddenError("scope_not_granted", correlation_id=context.correlation_id)
        return context

    @staticmethod
    def _selectors(grants: Iterable[WorkReadGrant]) -> tuple[DelegatedWorkSelector, ...]:
        return tuple(
            DelegatedWorkSelector(
                work_ids=grant.work_ids,
                arena_ids=grant.arena_ids,
                work_kinds=grant.work_kinds,
                include_archived=grant.include_archived,
            )
            for grant in grants
        )

    @staticmethod
    def _work_grants(
        context: AuthorityContext,
        *,
        operation: str,
        kind: str | None = None,
        relationship: str | None = None,
        include_archived: bool = False,
    ) -> tuple[WorkReadGrant, ...]:
        return tuple(
            grant
            for grant in context.read_grants
            if isinstance(grant, WorkReadGrant)
            and operation in grant.operations
            and (kind is None or kind in grant.work_kinds)
            and (relationship is None or relationship in grant.relationship_types)
            and (not include_archived or grant.include_archived)
        )

    def _work_admitted(
        self,
        kind: str,
        work_id: str,
        grants: tuple[WorkReadGrant, ...],
        *,
        archived: bool,
    ) -> bool:
        membership = None
        for grant in grants:
            if archived and not grant.include_archived:
                continue
            exact = not grant.work_ids or f"{kind}/{work_id}" in grant.work_ids
            if not exact:
                continue
            if grant.arena_ids:
                if membership is None:
                    try:
                        membership = self._arenas.membership(kind, work_id)
                    except MissingWorkObjectError:
                        return False
                if membership.arena is None or membership.arena.id not in grant.arena_ids:
                    continue
            return True
        return False

    @staticmethod
    def _not_found() -> None:
        raise KeyError("resource_not_granted")

    def _require_work(
        self,
        kind: str,
        work_id: str,
        grants: tuple[WorkReadGrant, ...],
    ):
        try:
            work = self._repository.get_by_id(kind, work_id)
        except MissingWorkObjectError:
            self._not_found()
        if not grants or not self._work_admitted(
            kind,
            work_id,
            grants,
            archived=work.archived,
        ):
            self._not_found()
        return work

    def _audit(self, context: AuthorityContext, operation: str, **safe_summary: Any) -> None:
        self._audit_log.record(
            actor_id=context.actor_id,
            session_id=context.session_id,
            correlation_id=context.correlation_id,
            category="delegated.read",
            operation=operation,
            safe_summary=safe_summary,
        )

    def graph(self, credential: str | None) -> dict[str, Any]:
        context = self.authorize(credential, SCOPE_GRAPH_READ)
        result = build_monitor_snapshot(self._storage)
        self._audit(context, "delegated_graph")
        return result

    def list_work(
        self,
        credential: str | None,
        kind: str,
        *,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ):
        context = self.authorize(credential, SCOPE_WORK_READ)
        grants = self._work_grants(
            context,
            operation="list",
            kind=kind,
            include_archived=include_archived,
        )
        if not grants:
            self._not_found()
        nodes = self._storage.delegated_work_page(
            kind,
            selectors=self._selectors(grants),
            work_kinds=(kind,),
            include_archived=include_archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        result = [self._repository._from_node(node) for node in nodes]
        self._audit(context, "delegated_work_list", result_count=len(result))
        return result

    def get_work(self, credential: str | None, kind: str, work_id: str):
        context = self.authorize(credential, SCOPE_WORK_READ)
        grants = self._work_grants(context, operation="get", kind=kind)
        result = self._require_work(kind, work_id, grants)
        self._audit(context, "delegated_work_get")
        return result

    def related_work(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        relationship: str,
        *,
        operation: str = "relationships",
        after_resource: str | None = None,
        limit: int = 50,
    ):
        context = self.authorize(credential, SCOPE_WORK_READ)
        direction = _RELATIONSHIPS.get(relationship)
        if direction is None or (relationship in {"blockers", "blocked"} and kind != "Task"):
            raise ValueError("invalid Work relationship")
        source_grants = self._work_grants(
            context,
            operation=operation,
            kind=kind,
            relationship=relationship,
        )
        self._require_work(kind, work_id, source_grants)
        target_grants = self._work_grants(
            context,
            operation=operation,
            relationship=relationship,
        )
        if not target_grants:
            return []
        edge, incoming = direction
        nodes = self._storage.delegated_related_work_nodes(
            kind,
            work_id,
            edge,
            incoming=incoming,
            selectors=self._selectors(target_grants),
            work_kinds=tuple(
                kind_name
                for kind_name in ("Proposal", "Initiative", "Project", "Issue", "Task")
                if any(kind_name in grant.work_kinds for grant in target_grants)
            ),
            include_archived=True,
            after_resource=after_resource,
            limit=limit,
        )
        result = [self._repository._from_node(node) for node in nodes]
        self._audit(context, f"delegated_work_{operation}", result_count=len(result))
        return result

    def list_arenas(
        self,
        credential: str | None,
        *,
        after_id: str | None = None,
        limit: int = 50,
    ):
        context = self.authorize(credential, SCOPE_ARENA_READ)
        arena_ids = tuple(
            sorted(
                {
                    arena_id
                    for grant in context.read_grants
                    if isinstance(grant, ArenaReadGrant)
                    for arena_id in grant.arena_ids
                }
            )
        )
        if not arena_ids:
            self._not_found()
        nodes = self._storage.delegated_arena_page(arena_ids, after_id=after_id, limit=limit)
        result = [self._arenas._from_node(node) for node in nodes]
        self._audit(context, "delegated_arena_list", result_count=len(result))
        return result

    def _require_arena(self, context: AuthorityContext, arena_id: str):
        allowed = any(
            isinstance(grant, ArenaReadGrant) and arena_id in grant.arena_ids
            for grant in context.read_grants
        )
        if not allowed:
            self._not_found()
        try:
            return self._arenas.get(arena_id)
        except MissingWorkObjectError:
            self._not_found()

    def get_arena(self, credential: str | None, arena_id: str):
        context = self.authorize(credential, SCOPE_ARENA_READ)
        result = self._require_arena(context, arena_id)
        self._audit(context, "delegated_arena_get")
        return result

    def arena_members(
        self,
        credential: str | None,
        arena_id: str,
        *,
        after_resource: str | None = None,
        limit: int = 50,
    ):
        context = self.authorize(credential, SCOPE_ARENA_READ)
        if SCOPE_WORK_READ not in context.scopes:
            raise ForbiddenError("scope_not_granted", correlation_id=context.correlation_id)
        self._require_arena(context, arena_id)
        grants = self._work_grants(context, operation="list")
        if not grants:
            self._not_found()
        kinds = tuple(
            kind
            for kind in ("Proposal", "Initiative", "Project", "Issue", "Task")
            if any(kind in grant.work_kinds for grant in grants)
        )
        containments = self._storage.delegated_arena_member_page(
            arena_id,
            selectors=self._selectors(grants),
            work_kinds=kinds,
            include_archived=True,
            after_resource=after_resource,
            limit=limit,
        )
        result = [self._repository._from_node(item.node) for item in containments]
        self._audit(context, "delegated_arena_members", result_count=len(result))
        return result

    def arena_membership(self, credential: str | None, kind: str, work_id: str):
        context = self.authorize(credential, SCOPE_ARENA_READ)
        if SCOPE_WORK_READ not in context.scopes:
            raise ForbiddenError("scope_not_granted", correlation_id=context.correlation_id)
        grants = self._work_grants(context, operation="get", kind=kind)
        self._require_work(kind, work_id, grants)
        membership = self._arenas.membership(kind, work_id)
        if membership.arena is not None:
            self._require_arena(context, membership.arena.id)
        self._audit(context, "delegated_arena_membership")
        return membership

    def list_observations(
        self,
        credential: str | None,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ):
        context = self.authorize(credential, SCOPE_OBSERVATION_READ)
        result = self._observations.query(descending=descending, after_id=after_id, limit=limit)
        self._audit(context, "delegated_observation_list", result_count=len(result))
        return result

    def get_observation(self, credential: str | None, observation_id: str):
        context = self.authorize(credential, SCOPE_OBSERVATION_READ)
        result = self._observations.get_by_id(observation_id)
        self._audit(context, "delegated_observation_get")
        return result

    def supporting_material(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        *,
        limit: int = 50,
        after: str | None = None,
    ):
        context = self.authorize(credential, SCOPE_MATERIAL_READ)
        if SCOPE_WORK_READ not in context.scopes:
            raise ForbiddenError("scope_not_granted", correlation_id=context.correlation_id)
        grants = self._work_grants(context, operation="get", kind=kind)
        self._require_work(kind, work_id, grants)
        result = self._supporting.read(kind, work_id, limit=limit, after=after)
        self._audit(context, "delegated_supporting_material")
        return result

    def work_document(
        self,
        credential: str | None,
        kind: str,
        work_id: str,
        artifact_id: str,
    ) -> WorkDocumentEnvelope:
        context = self.authorize(credential, SCOPE_DOCUMENT_READ)
        for scope in (SCOPE_MATERIAL_READ, SCOPE_WORK_READ):
            if scope not in context.scopes:
                raise ForbiddenError("scope_not_granted", correlation_id=context.correlation_id)
        grants = self._work_grants(context, operation="get", kind=kind)
        self._require_work(kind, work_id, grants)
        item = self._supporting.attached_artifact(kind, work_id, artifact_id)
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
        self._audit(context, "delegated_work_document")
        return result

    def query(self, credential: str | None, request_json: bytes) -> dict[str, Any]:
        context = self.authorize(credential, SCOPE_QUERY_READ)
        if self._cypher_read is None:
            from devgraph.cypher_read import CypherReadError

            raise CypherReadError("cypher_backend_unavailable", 503)
        result = self._cypher_read.execute_delegated(
            credential=credential,
            request_json=request_json,
            verifier=self._verifier,
        )
        self._audit(context, "delegated_query", row_count=result["row_count"])
        return result
