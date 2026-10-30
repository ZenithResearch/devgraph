"""Emit real public shells/empty scoped projections for disposable browser QA."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.arena_access import CollectionArenaReadGrant
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.monitor_read import MonitorWorkGrant, project_monitor_read
from devgraph.storage.memory import MemoryGraphStorage

storage = MemoryGraphStorage()
grant = MonitorWorkGrant.model_validate(
    {
        "scope": "devgraph.work.read",
        "operations": ["list", "get"],
        "relationship_types": ["parent"],
        "work_kinds": ["Task"],
        "include_archived": False,
    }
)
app = FastAPI()
register_wallet_monitor(app, storage=storage, enrollment=SimpleNamespace(), audit_log=AuditLog())
with TestClient(app, base_url="https://graph.example") as client:
    shells = {}
    for path in ("/login", "/monitor", "/monitor/kanban/"):
        response = client.get(path)
        shells[path] = {"body": response.text, "headers": dict(response.headers)}
reads = {
    path: project_monitor_read(storage, (grant,
        CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True)), path)
    for path in (
        "/monitor/topology/v1",
        "/todos/v2",
        "/todos/v2/classification-report",
        "/workflows/v1",
        "/monitor/kanban/v2",
    )
}
print(json.dumps(jsonable_encoder({"shells": shells, "reads": reads})))
