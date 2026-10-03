"""Read-only UI preview through the installed, authenticated Work API.

No database access, local Work writes, migrations, or signing routes. The legacy
adapter is admitted only for schema 26, where workflow metadata does not exist.
Its short-lived projection uses the same board filters/counts as the new host.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from devgraph.api.topology import _ASSETS
from devgraph.frontend import FRONTEND_HTML
from devgraph.frontend.selection import _ASSETS as SELECTION_ASSETS
from devgraph.frontend.selection import _HEADERS
from devgraph.kanban import BoardChanged, BoardFilter, InvalidBoardFilter, build_board
from devgraph.local_host import load_local_config, read_local_read_credential
from devgraph.model.base import WorkStatus
from devgraph.model.repository import WORK_OBJECT_TYPES, MissingWorkObjectError
from devgraph.model.validation import validate_work_object_id
from devgraph.storage.base import EdgeRecord, NodeRecord, StorageUnavailable
from devgraph.topology import (
    SOURCE_EDGE_LIMIT,
    SOURCE_NODE_LIMIT,
    InvalidTopologyFilter,
    TopologyFilter,
    filter_projection,
)
from devgraph.workflow_contract import WORK_KINDS, catalog, default_workflow

ORIGIN = "http://127.0.0.1:4193"
UPSTREAM = "http://127.0.0.1:8080"
COOKIE = "devgraph_kanban_reader"
ROOT = files("devgraph.frontend").joinpath("static")
HOURS = 4 * 60 * 60


def monitor_html():
    # Adapt only the development shell to the same HttpOnly reader session used
    # by Kanban. The fixed host still validates the real read credential.
    replacements = {
        "if (!tokenInput.value.trim()) { "
        "document.getElementById('connection-settings').open = true; "
        "text('status-line', 'Enter a read access key to connect.'); return; }": "",
        "const credential = tokenInput.value.trim(); "
        "if (!credential || graphView.preferenceKey) return;": (
            "const credential = tokenInput.value.trim() || 'local-reader-session'; "
            "if (graphView.preferenceKey) return;"
        ),
        "schedule(); if (tokenInput.value) refresh();": "schedule(); refresh();",
        '<option value="10" selected>10 seconds</option>': '<option value="10">10 seconds</option>'
        '<option value="60" selected>60 seconds</option>',
        "}, 30000);": "}, 120000);",
        "Live local state": "Live local data · development preview",
    }
    html = FRONTEND_HTML
    for old, new in replacements.items():
        if old not in html:
            raise ValueError("The monitor development adapter needs updating")
        html = html.replace(old, new)
    return html


class ReadProjection:
    """Ephemeral API response index; deliberately has no mutation methods."""

    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges
        self.read_at = datetime.now(timezone.utc).isoformat()

    def query(self, kind, *, archived=None, limit=100, after_id=None):
        return [
            n
            for n in self.nodes
            if n.label == kind
            and (after_id is None or n.id > after_id)
            and (archived is None or n.archived == archived)
        ][:limit]

    def list_edges(self, relationship, *, limit):
        return [e for e in self.edges if e.relationship == relationship][:limit]


async def upstream(client, path, *, payload=None):
    response = await (client.get(path) if payload is None else client.post(path, json=payload))
    if response.status_code in {401, 403, 404, 409, 412, 429}:
        raise HTTPException(response.status_code, "Local Devgraph read unavailable")
    if response.status_code != 200:
        raise HTTPException(502, "Local Devgraph read unavailable")
    return response.json()


async def read_edges(client, relationship, a, b):
    # Lexicographic pagination without OR (outside the bounded query language).
    # Finish the final source's remaining targets, then advance to later sources.
    edges, source, target = [], None, None

    async def page(where, parameters):
        result = await upstream(
            client,
            "/query/cypher",
            payload={
                "schema": "devgraph.cypher-read-request.v1",
                "query": f"MATCH (a:{a})-[:{relationship}]->(b:{b}) {where} "
                "RETURN a.id AS source, b.id AS target "
                "ORDER BY source ASC, target ASC LIMIT 100",
                "parameters": parameters,
            },
        )
        return result["rows"]

    while True:
        rows = []
        remaining_sources = False
        if source is not None:
            rows = await page(
                "WHERE a.id = $source AND b.id > $target", {"source": source, "target": target}
            )
        if not rows:
            remaining_sources = True
            rows = await page(
                "WHERE a.id > $source" if source is not None else "",
                {"source": source} if source is not None else {},
            )
        if not rows:
            return edges
        previous = (source, target)
        source, target = rows[-1]
        if previous[0] is not None and (source, target) <= previous:
            raise ValueError("Relationship pagination did not advance")
        edges.extend(EdgeRecord(a, s, relationship, b, t) for s, t in rows)
        if len(edges) > 10000:
            raise ValueError("Relationship budget exceeded")
        if remaining_sources and len(rows) < 100:
            return edges


async def read_projection(client):
    ready = await upstream(client, "/ready")
    if not ready.get("ready") or ready.get("current_applied_version") != 26:
        raise HTTPException(503, "Use the installed workflow API after upgrading this host")
    nodes = []
    for kind in WORK_KINDS:
        after = None
        while True:
            path = f"/work/{kind}?limit=100&include_archived=true"
            if after is not None:
                path += "&after_id=" + after
            items = (await upstream(client, path))["items"]
            for row in items:
                if row["kind"] != kind:
                    raise ValueError("Unexpected Work kind")
                # Timestamps are not part of the legacy envelope or the board.
                # These temporary objects never leave this read-only projection.
                work = WORK_OBJECT_TYPES[kind](
                    **{
                        k: v
                        for k, v in row.items()
                        if k not in {"kind", "status", "artifact_ids", "external_link_ids"}
                    },
                    status=WorkStatus(row["status"]),
                    artifact_ids=tuple(row["artifact_ids"]),
                    external_link_ids=tuple(row["external_link_ids"]),
                    workflow_json=None,
                )
                nodes.append(
                    NodeRecord(
                        kind, work.id, work.to_node_properties(), work.status == WorkStatus.ARCHIVED
                    )
                )
            if len(nodes) > 10000:
                raise ValueError("Work budget exceeded")
            if len(items) < 100:
                break
            cursor = items[-1]["id"]
            if after is not None and cursor <= after:
                raise ValueError("Work pagination did not advance")
            after = cursor
    edges = []
    for a, b in (("Initiative", "Project"), ("Project", "Issue"), ("Issue", "Task")):
        edges.extend(await read_edges(client, "HAS_CHILD", a, b))
    edges.extend(await read_edges(client, "BLOCKS", "Task", "Task"))
    return ReadProjection(sorted(nodes, key=lambda n: (n.label, n.id)), edges)


def create_preview(ticket, credential_reader, *, clock=time.monotonic, transport=None):
    if not re.fullmatch(r"[a-f0-9]{64}", ticket):
        raise ValueError("A private 256-bit connection ticket is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session, used, started = secrets.token_hex(32), False, clock()
    cached, lock = {}, asyncio.Lock()
    snapshot_cache, snapshot_lock = {}, asyncio.Lock()

    @app.middleware("http")
    async def boundary(request, call_next):
        if (
            request.headers.get("host") != "127.0.0.1:4193"
            or request.headers.get("origin", ORIGIN) != ORIGIN
            or request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}
        ):
            return JSONResponse({"detail": "Local reader origin mismatch"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and request.url.path != "/preview/session":
            return JSONResponse({"detail": "This preview only reads Work"}, status_code=405)
        try:
            response = await call_next(request)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
            return JSONResponse(
                {"detail": "Unable to read the local graph. Try again."},
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/preview/session")
    async def connect(request: Request):
        nonlocal used
        if (
            used
            or clock() - started > 600
            or not secrets.compare_digest(
                request.headers.get("authorization", ""), "Bearer " + ticket
            )
        ):
            raise HTTPException(401, "Local connection ticket expired")
        used = True
        response = Response(status_code=204)
        response.set_cookie(
            COOKIE, session, max_age=HOURS, httponly=True, samesite="strict", path="/"
        )
        return response

    def credential(request):
        bearer = request.headers.get("authorization", "")
        if bearer.startswith("Bearer ") and bearer[7:].strip():
            return bearer[7:]
        if (
            used
            and clock() - started < HOURS
            and secrets.compare_digest(request.cookies.get(COOKIE, ""), session)
        ):
            return credential_reader()
        raise HTTPException(401, "Connect with your local read key")

    def client(key):
        return httpx.AsyncClient(
            base_url=UPSTREAM,
            trust_env=False,
            follow_redirects=False,
            timeout=20,
            transport=transport,
            headers={"Authorization": "Bearer " + key},
        )

    async def projection(request):
        key = credential(request)
        async with client(key) as api:
            # Revalidate on every request, including cached responses after revocation.
            await upstream(api, "/work/Task?limit=1&include_archived=true")
            digest = hashlib.sha256(key.encode()).hexdigest()
            async with lock:
                if cached.get("key") != digest or clock() - cached.get("time", -60) > 60:
                    value = await asyncio.wait_for(read_projection(api), timeout=240)
                    cached.update(key=digest, time=clock(), value=value)
                return cached["value"]

    async def snapshot(request):
        key = credential(request)
        async with client(key) as api:
            await upstream(api, "/work/Task?limit=1&include_archived=true")
            digest = hashlib.sha256(key.encode()).hexdigest()
            async with snapshot_lock:
                if (
                    snapshot_cache.get("key") != digest
                    or clock() - snapshot_cache.get("time", -60) > 60
                ):
                    api.timeout = httpx.Timeout(120)
                    value = await upstream(api, "/monitor/snapshot")
                    if (
                        len(value["graph_nodes"]) > SOURCE_NODE_LIMIT
                        or len(value["graph_edges"]) > SOURCE_EDGE_LIMIT
                    ):
                        raise HTTPException(503, "The graph exceeds this preview's read budget")
                    snapshot_cache.update(key=digest, time=clock(), value=value)
                return snapshot_cache["value"]

    @app.get("/")
    async def home():
        return RedirectResponse("/monitor")

    @app.get("/monitor")
    @app.get("/monitor/")
    async def monitor():
        # The monitor's trusted, repo-owned shell contains inline scripts/styles.
        headers = {
            **_HEADERS,
            "Content-Security-Policy": _HEADERS["Content-Security-Policy"].replace(
                "script-src 'self'; style-src 'self';",
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';",
            ),
        }
        return Response(monitor_html(), media_type="text/html", headers=headers)

    @app.get("/monitor/snapshot")
    async def monitor_snapshot(request: Request):
        return await snapshot(request)

    @app.get("/monitor/topology/v1")
    async def topology(request: Request):
        try:
            filters = TopologyFilter.parse(request.url.query)
        except InvalidTopologyFilter:
            raise HTTPException(400, "Invalid topology filter") from None
        return filter_projection(await snapshot(request), filters)

    @app.get("/monitor/selection")
    @app.get("/monitor/selection/")
    async def selection():
        html = (
            ROOT.joinpath("selection/page/index.html")
            .read_text()
            .replace(
                "<head>", '<head><meta name="devgraph-selection-reader" content="local-session">'
            )
        )
        return Response(html, media_type="text/html", headers=_HEADERS)

    @app.get("/monitor/selection-assets/{name:path}")
    async def selection_asset(name: str):
        if name not in SELECTION_ASSETS:
            raise HTTPException(404)
        body = ROOT.joinpath("selection", name).read_text()
        if name == "page/app.mjs":
            body = body.replace("'http://127.0.0.1:8080/monitor'", "'/monitor'")
            body = body.replace("cookieReader ? 30000 : 15000", "cookieReader ? 120000 : 15000")
        return Response(body, media_type=SELECTION_ASSETS[name], headers=_HEADERS)

    @app.get("/monitor/kanban")
    async def redirect():
        return RedirectResponse("/monitor/kanban/")

    @app.get("/monitor/kanban/")
    async def page():
        html = ROOT.joinpath("kanban/index.html").read_text()
        html = html.replace("<head>", '<head><meta name="devgraph-local-reader" content="true">')
        html = html.replace('id="wallet-connect"', 'id="wallet-connect" hidden disabled')
        html = html.replace(
            "Reading mode · signed moves require Chrome and Castalia Wallet.",
            "Live local data · read-only preview",
        )
        html = html.replace("FROM IDEA TO VERIFIED HANDOFF", "YOUR DEVGRAPH · LIVE LOCAL DATA")
        html = html.replace(
            '<p class="board-note">Columns summarize',
            '<p class="board-note">This host has no workflow stages yet. '
            "Older work appears in Backlog with “Stage not set”. No records are changed.</p>"
            '<p class="board-note">Columns summarize',
        )
        return Response(html, media_type="text/html", headers=_HEADERS)

    @app.get("/monitor/{group}-assets/{name}")
    async def asset(group: str, name: str):
        allowed = (
            _ASSETS
            if group == "topology"
            else {
                "style.css": "text/css",
                "app.mjs": "text/javascript",
                "core.mjs": "text/javascript",
                "wallet.mjs": "text/javascript",
            }
            if group == "kanban"
            else {}
        )
        if name not in allowed:
            raise HTTPException(404)
        return Response(
            ROOT.joinpath(group, name).read_bytes(), media_type=allowed[name], headers=_HEADERS
        )

    @app.get("/workflows/v1")
    async def workflows(request: Request):
        async with client(credential(request)) as api:
            await upstream(api, "/work/Task?limit=1&include_archived=true")
        return catalog()

    @app.get("/monitor/kanban/v1")
    async def board(request: Request):
        try:
            filters = BoardFilter.parse(request.url.query)
            data = await projection(request)
            return {**build_board(data, filters), "read_at": data.read_at}
        except InvalidBoardFilter:
            raise HTTPException(400, "Invalid board filter") from None
        except BoardChanged:
            raise HTTPException(409, "The graph changed; refresh to continue") from None
        except MissingWorkObjectError:
            raise HTTPException(404, "This scope no longer exists") from None
        except StorageUnavailable:
            raise HTTPException(503, "The graph exceeds this preview's read budget") from None

    @app.get("/work/{kind}/{work_id}/workflow")
    async def workflow(request: Request, kind: str, work_id: str):
        data = await projection(request)
        node = next((n for n in data.nodes if (n.label, n.id) == (kind, work_id)), None)
        if node is None:
            raise HTTPException(404)
        children = [
            e
            for e in data.edges
            if e.relationship == "HAS_CHILD" and (e.from_label, e.from_id) == (kind, work_id)
        ]
        return {
            "schema": "devgraph.work-workflow.v1",
            "kind": kind,
            "id": work_id,
            "version": str(node.properties["version"]),
            "workflow": None,
            "default_workflow": default_workflow(kind),
            "transitions": [],
            "child_progress": {"done": 0, "total": len(children)},
            "completion_gaps": ["Stage not set"],
            "current_reviews": {},
        }

    @app.get("/work/{kind}/{work_id}")
    @app.get("/work/{kind}/{work_id}/{tail:path}")
    async def work(request: Request, kind: str, work_id: str, tail: str = ""):
        validate_work_object_id(work_id)
        if kind not in WORK_KINDS or not (
            not tail
            or tail == "supporting-material"
            or tail in {
                "relationships/children", "relationships/parent", "relationships/dependencies",
                "relationships/dependents", "relationships/blockers", "relationships/blocked",
            }
            or re.fullmatch(r"supporting-material/Artifact/[A-Za-z0-9_.:-]+/document", tail)
        ):
            raise HTTPException(404)
        async with client(credential(request)) as api:
            return await upstream(api, request.url.path + "?" + request.url.query)

    @app.get("/initiative-observations")
    @app.get("/initiative-observations/{item_id}")
    @app.get("/arenas/{item_id}")
    async def read_detail(request: Request, item_id: str | None = None):
        if item_id is not None:
            validate_work_object_id(item_id)
        async with client(credential(request)) as api:
            return await upstream(api, request.url.path + "?" + request.url.query)

    return app


def from_environment():
    path = Path(os.environ["DEVGRAPH_KANBAN_SESSION_FILE"])
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Connection ticket must be an owner-private regular file")
    return create_preview(
        path.read_text().strip(), lambda: read_local_read_credential(load_local_config())
    )
