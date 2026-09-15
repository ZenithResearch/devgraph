"""Read-only Arena routes; all writes use the signed Arena operation endpoint."""

from fastapi import FastAPI, Header, Query, Response

from devgraph.api.app import ApiServices
from devgraph.api.routes import _credential, _envelope
from devgraph.api.schemas import WorkKind, WorkObjectListEnvelope
from devgraph.arena_contract import Arena, ArenaList, ArenaMembership


def register_arenas(app: FastAPI, services: ApiServices):
    graph = services.authorized_graph

    @app.get("/arenas", response_model=ArenaList)
    def list_arenas(response: Response, include_archived: bool = False,
                    after_id: str | None = None, limit: int = Query(default=50, ge=1, le=100),
                    authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return ArenaList(items=tuple(graph.read_arenas(_credential(authorization),
            include_archived=include_archived, after_id=after_id, limit=limit)))

    @app.get("/arenas/{arena_id}", response_model=Arena)
    def get_arena(arena_id: str, response: Response,
                  authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return graph.read_arenas(_credential(authorization), arena_id=arena_id)

    @app.get("/arenas/{arena_id}/members", response_model=WorkObjectListEnvelope)
    def members(arena_id: str, response: Response, after_resource: str | None = None,
                limit: int = Query(default=50, ge=1, le=100),
                authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return WorkObjectListEnvelope(items=[_envelope(work) for work in graph.arena_members(
            _credential(authorization), arena_id, after_resource=after_resource, limit=limit)])

    @app.get("/work/{kind}/{work_id}/arena", response_model=ArenaMembership)
    def membership(kind: WorkKind, work_id: str, response: Response,
                   authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return graph.arena_membership(_credential(authorization), kind, work_id)
