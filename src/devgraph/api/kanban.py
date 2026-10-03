"""Protected board reads and allowlisted page assets; no bearer write route."""

from importlib.resources import files

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from devgraph.api.routes import _credential
from devgraph.api.schemas import WorkKind
from devgraph.frontend.selection import _HEADERS


def register_kanban(app: FastAPI, services):
    graph = services.authorized_graph
    assets = {
        "index.html": "text/html",
        "style.css": "text/css",
        "app.mjs": "text/javascript",
        "core.mjs": "text/javascript",
        "wallet.mjs": "text/javascript",
    }

    def asset(name):
        if name not in assets:
            raise HTTPException(404)
        return Response(
            files("devgraph.frontend").joinpath("static/kanban", name).read_bytes(),
            media_type=assets[name],
            headers=_HEADERS,
        )

    @app.get("/monitor/kanban", include_in_schema=False)
    def canonical_page():
        return RedirectResponse("/monitor/kanban/", headers=_HEADERS)

    @app.get("/monitor/kanban/", include_in_schema=False)
    def page():
        return asset("index.html")

    @app.get("/monitor/kanban-assets/{name}", include_in_schema=False)
    def static(name: str):
        return asset(name)

    @app.get("/monitor/kanban/v1")
    def board(
        request: Request, response: Response, authorization: str | None = Header(default=None)
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.kanban(_credential(authorization), request.url.query)

    @app.get("/workflows/v1")
    def workflows(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return graph.workflows(_credential(authorization))

    @app.get("/work/{kind}/{work_id}/workflow")
    def workflow(
        kind: WorkKind,
        work_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.workflows(_credential(authorization), kind=kind, work_id=work_id)
