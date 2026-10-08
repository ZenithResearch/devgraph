"""HTTP adapter for the exact allowlisted delegated Devgraph read surface."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.concurrency import run_in_threadpool

from devgraph.api.routes import _credential, _envelope, _observation_envelope
from devgraph.api.schemas import (
    InitiativeObservationEnvelope,
    InitiativeObservationListEnvelope,
    MonitorSnapshotEnvelope,
    SupportingMaterialEnvelope,
    WorkDocumentEnvelope,
    WorkKind,
    WorkObjectEnvelope,
    WorkObjectListEnvelope,
)
from devgraph.arena_contract import Arena, ArenaList, ArenaMembership
from devgraph.auth.delegated_contract import SCOPE_QUERY_READ
from devgraph.auth.delegated_read import DelegatedReadService
from devgraph.auth.errors import UnauthenticatedError
from devgraph.cypher_read import MAX_REQUEST_BYTES, CypherReadError


def register_delegated_read(app: FastAPI, service: DelegatedReadService) -> None:
    @app.middleware("http")
    async def delegated_response_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/devgraph" or path.startswith("/devgraph/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Authorization, Origin"
        return response

    @app.get("/devgraph/graph", response_model=MonitorSnapshotEnvelope)
    def graph(authorization: str | None = Header(default=None)):
        return service.graph(_credential(authorization))

    @app.get("/devgraph/work/{kind}", response_model=WorkObjectListEnvelope)
    def list_work(
        kind: WorkKind,
        include_archived: bool = False,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return WorkObjectListEnvelope(
            items=[
                _envelope(item)
                for item in service.list_work(
                    _credential(authorization),
                    kind,
                    include_archived=include_archived,
                    descending=descending,
                    after_id=after_id,
                    limit=limit,
                )
            ]
        )

    @app.get("/devgraph/work/{kind}/{work_id}", response_model=WorkObjectEnvelope)
    def get_work(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _envelope(service.get_work(_credential(authorization), kind, work_id))

    @app.get(
        "/devgraph/work/{kind}/{work_id}/relationships/{relationship}",
        response_model=WorkObjectListEnvelope,
    )
    def relationships(
        kind: WorkKind,
        work_id: str,
        relationship: Literal[
            "children", "parent", "dependencies", "dependents", "blockers", "blocked"
        ],
        after_resource: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return WorkObjectListEnvelope(
            items=[
                _envelope(item)
                for item in service.related_work(
                    _credential(authorization),
                    kind,
                    work_id,
                    relationship,
                    after_resource=after_resource,
                    limit=limit,
                )
            ]
        )

    @app.get("/devgraph/work/{kind}/{work_id}/children")
    def children(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[WorkObjectEnvelope]:
        return [
            _envelope(item)
            for item in service.related_work(
                _credential(authorization),
                kind,
                work_id,
                "children",
                operation="children",
                limit=100,
            )
        ]

    @app.get("/devgraph/tasks/{task_id}/blockers")
    def blockers(
        task_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[WorkObjectEnvelope]:
        return [
            _envelope(item)
            for item in service.related_work(
                _credential(authorization),
                "Task",
                task_id,
                "blockers",
                operation="blockers",
                limit=100,
            )
        ]

    @app.get("/devgraph/arenas", response_model=ArenaList)
    def arenas(
        after_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return ArenaList(
            items=tuple(
                service.list_arenas(_credential(authorization), after_id=after_id, limit=limit)
            )
        )

    @app.get("/devgraph/arenas/{arena_id}", response_model=Arena)
    def arena(arena_id: str, authorization: str | None = Header(default=None)):
        return service.get_arena(_credential(authorization), arena_id)

    @app.get("/devgraph/arenas/{arena_id}/members", response_model=WorkObjectListEnvelope)
    def arena_members(
        arena_id: str,
        after_resource: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return WorkObjectListEnvelope(
            items=[
                _envelope(item)
                for item in service.arena_members(
                    _credential(authorization),
                    arena_id,
                    after_resource=after_resource,
                    limit=limit,
                )
            ]
        )

    @app.get("/devgraph/work/{kind}/{work_id}/arena", response_model=ArenaMembership)
    def arena_membership(
        kind: WorkKind,
        work_id: str,
        authorization: str | None = Header(default=None),
    ):
        return service.arena_membership(_credential(authorization), kind, work_id)

    @app.get(
        "/devgraph/initiative-observations",
        response_model=InitiativeObservationListEnvelope,
    )
    def observations(
        descending: bool = False,
        after_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        return InitiativeObservationListEnvelope(
            items=[
                _observation_envelope(item)
                for item in service.list_observations(
                    _credential(authorization),
                    descending=descending,
                    after_id=after_id,
                    limit=limit,
                )
            ]
        )

    @app.get(
        "/devgraph/initiative-observations/{observation_id}",
        response_model=InitiativeObservationEnvelope,
    )
    def observation(
        observation_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _observation_envelope(
            service.get_observation(_credential(authorization), observation_id)
        )

    @app.get(
        "/devgraph/work/{kind}/{work_id}/supporting-material",
        response_model=SupportingMaterialEnvelope,
    )
    def supporting_material(
        kind: WorkKind,
        work_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        after: str | None = Query(default=None, max_length=4096),
        authorization: str | None = Header(default=None),
    ):
        return service.supporting_material(
            _credential(authorization), kind, work_id, limit=limit, after=after
        )

    @app.get(
        "/devgraph/work/{kind}/{work_id}/supporting-material/Artifact/{artifact_id}/document",
        response_model=WorkDocumentEnvelope,
    )
    def work_document(
        kind: WorkKind,
        work_id: str,
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ):
        return service.work_document(_credential(authorization), kind, work_id, artifact_id)

    @app.post("/devgraph/query/cypher")
    async def query(request: Request):
        selected: dict[bytes, str] = {}
        for name, value in request.scope.get("headers", []):
            name = name.lower()
            if name not in {b"authorization", b"content-type", b"content-encoding"}:
                continue
            if name in selected or len(value) > 4096:
                raise UnauthenticatedError("invalid_request")
            try:
                selected[name] = value.decode("ascii")
            except UnicodeError:
                raise UnauthenticatedError("invalid_request") from None
        credential = _credential(selected.get(b"authorization"))
        await run_in_threadpool(service.authorize, credential, SCOPE_QUERY_READ)
        if (
            request.scope.get("query_string")
            or b"content-encoding" in selected
            or selected.get(b"content-type", "").lower().split(";")[0].strip() != "application/json"
        ):
            raise CypherReadError("invalid_cypher_read_transport")

        async def body() -> bytes:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                    raise CypherReadError("cypher_request_too_large", 413)
                raw.extend(chunk)
            return bytes(raw)

        try:
            raw = await asyncio.wait_for(body(), timeout=5)
            return await asyncio.wait_for(
                run_in_threadpool(service.query, credential, raw), timeout=8
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise CypherReadError("cypher_query_timeout", 504) from None
