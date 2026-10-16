"""Protected board reads and allowlisted page assets; no bearer write route."""

from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from devgraph.api.routes import _credential
from devgraph.frontend.selection import _HEADERS as _BASE_HEADERS

_HEADERS = {
    **_BASE_HEADERS,
    "Content-Security-Policy": _BASE_HEADERS["Content-Security-Policy"].replace(
        "script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'"
    ),
}


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

    @app.get("/assets/kanban/{name}", include_in_schema=False)
    @app.get("/monitor/kanban-assets/{name}", include_in_schema=False)
    def static(name: str):
        return asset(name)

    # wallet.mjs statically imports ../credential-v2/*.mjs, which the browser
    # resolves under /monitor/. Serve the same allowlisted shared modules here.
    shared = {"provider-setup.mjs", "subject.mjs"}

    @app.get("/monitor/credential-v2/{name}", include_in_schema=False)
    def shared_credential_module(name: str):
        if name not in shared:
            raise HTTPException(404)
        return Response(
            files("devgraph.frontend").joinpath("static/credential-v2", name).read_bytes(),
            media_type="text/javascript",
            headers=_HEADERS,
        )

    @app.get("/monitor/kanban/v1")
    def board(
        request: Request, response: Response, authorization: str | None = Header(default=None)
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.kanban(_credential(authorization), request.url.query)

    @app.get("/monitor/kanban/v2")
    def board_v2(
        request: Request, response: Response, authorization: str | None = Header(default=None)
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.kanban(_credential(authorization), request.url.query, version=2)

    @app.get("/workflows/v1")
    def workflows(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        return graph.workflows(_credential(authorization))

    @app.get("/work/{kind}/{work_id}/workflow")
    def workflow(
        kind: Literal["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"],
        work_id: str,
        response: Response,
        authorization: str | None = Header(default=None),
    ):
        response.headers["Cache-Control"] = "no-store"
        return graph.workflows(_credential(authorization), kind=kind, work_id=work_id)
