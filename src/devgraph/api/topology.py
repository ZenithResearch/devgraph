"""Thin topology transport and allowlisted, packaged UI assets."""

from importlib.resources import files

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from devgraph.api.routes import (
    _credential,
    _empty_monitor_request_body,
    _monitor_authority_headers,
    _raw_path_query,
)
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.secs_monitor_view_read import (
    DEVGRAPH_MONITOR_VIEW_READ_HOST_V1,
    SecSMonitorViewReadDenied,
)
from devgraph.topology import ENUMS, PATH

_ASSETS = {
    "core.js": "text/javascript",
    "canvas.js": "text/javascript",
    "surface.js": "text/javascript",
    "reader.js": "text/javascript",
    "worker.js": "text/javascript",
    "style.css": "text/css",
    "proof.js": "text/javascript",
}


def register_topology(app: FastAPI, services) -> None:
    @app.get("/monitor/topology-assets/{name}", include_in_schema=False)
    def asset(name: str):
        if name not in _ASSETS:
            raise HTTPException(404)
        return Response(
            files("devgraph.frontend").joinpath("static/topology", name).read_bytes(),
            media_type=_ASSETS[name],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    parameters = [
        {
            "name": key,
            "in": "query",
            "required": False,
            "description": "Repeat for OR; omit for all; use none for an empty selection.",
            "schema": {"type": "array", "items": {"type": "string", "enum": [*values, "none"]}},
            "style": "form",
            "explode": True,
        }
        for key, values in ENUMS.items()
    ]
    parameters += [
        {
            "name": key,
            "in": "query",
            "required": False,
            "description": description,
            "schema": schema,
        }
        for key, description, schema in (
            (
                "relationship",
                "Repeat stored relationship types; none hides all lines.",
                {"type": "array", "items": {"type": "string"}},
            ),
            (
                "archived",
                "Archive visibility.",
                {"type": "string", "enum": ["include", "exclude", "only"]},
            ),
            ("arena", "Arena:<id> for directed reachability; * for any Arena.", {"type": "string"}),
            ("anchor", "Stable node key for its one-hop connected view.", {"type": "string"}),
            (
                "q",
                "Literal case-insensitive title or ID filter.",
                {"type": "string", "maxLength": 200},
            ),
            (
                "node_limit",
                "Maximum returned items; partial results are disclosed.",
                {"type": "integer", "minimum": 1, "maximum": 5000, "default": 5000},
            ),
            (
                "edge_limit",
                "Maximum returned connections.",
                {"type": "integer", "minimum": 1, "maximum": 50000, "default": 20000},
            ),
        )
    ]

    @app.get(PATH, openapi_extra={"parameters": parameters})
    async def topology(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        auth, origin, session, proof, host, length, transfer = _monitor_authority_headers(request)
        target = _raw_path_query(request)
        if any(x is not None for x in (origin, session, proof)):
            if (
                auth is not None
                or session is None
                or proof is None
                or origin is None
                or host != DEVGRAPH_MONITOR_VIEW_READ_HOST_V1
                or length
                or transfer
            ):
                raise SecSMonitorViewReadDenied("incomplete_monitor_proof")
            if services.monitor_topology_read is None:
                raise UnauthenticatedError("topology proof verification unavailable")
            return await run_in_threadpool(
                services.monitor_topology_read.execute,
                signed_session_header=session,
                request_proof_header=proof,
                method=request.method,
                path_query=target,
                origin=origin,
                body=await _empty_monitor_request_body(request),
            )
        return await run_in_threadpool(
            services.authorized_graph.monitor_topology, _credential(auth), target.partition("?")[2]
        )
