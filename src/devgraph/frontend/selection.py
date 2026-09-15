"""Selection page and its shared, dependency-free SDK modules."""

from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

_ROOT = files("devgraph.frontend").joinpath("static/selection")
_ASSETS = {
    "page/index.html": "text/html",
    "page/style.css": "text/css",
    "page/app.mjs": "text/javascript",
    "page/components.mjs": "text/javascript",
    "page/worker.mjs": "text/javascript",
    "page/example.mjs": "text/javascript",
    "page/flow-view.mjs": "text/javascript",
    "page/flow-layout.mjs": "text/javascript",
    **{f"core/{name}.mjs": "text/javascript" for name in (
        "index", "contracts", "adapter", "scoring", "expression", "closure",
        "run", "network", "draft",
    )},
}
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "worker-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def register_selection(app: FastAPI) -> None:
    def asset(name: str) -> Response:
        if name not in _ASSETS:
            raise HTTPException(status_code=404)
        return Response(
            _ROOT.joinpath(name).read_bytes(), media_type=_ASSETS[name], headers=_HEADERS,
        )

    @app.get("/monitor/selection", include_in_schema=False)
    @app.get("/monitor/selection/", include_in_schema=False)
    def selection_page():
        return asset("page/index.html")

    @app.get("/monitor/selection-assets/{name:path}", include_in_schema=False)
    def selection_asset(name: str):
        return asset(name)
