"""Public monitor shell and 1183 synthetic Work records; no production access."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from tests.model.test_arena_runtime import add_arena

from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.arenas import ArenaMutations
from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.monitor_read import project_monitor_read
from devgraph.auth.native_read import RESOURCE
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project, Task
from devgraph.storage.memory import MemoryGraphStorage

storage = MemoryGraphStorage()
for i in range(6):
    add_arena(ArenaMutations(storage), f"arena-{i}")
repo = WorkObjectRepository(storage)
previous = None
for kind, count in [(Initiative, 34), (Project, 123), (Issue, 369), (Task, 657)]:
    for i in range(count):
        name = f"{kind.__name__.lower()}-{i}"
        repo.create(kind(id=name, title=f"Example {kind.__name__} {i}"))
        if previous:
            parent_kind, parent_count = previous
            storage.create_edge(
                parent_kind.__name__,
                f"{parent_kind.__name__.lower()}-{i % parent_count}",
                "HAS_CHILD",
                kind.__name__,
                name,
            )
        else:
            storage.create_edge("Arena", f"arena-{i % 6}", "CONTAINS_WORK", "Initiative", name)
    previous = kind, count
work = dict(
    scope="devgraph.work.read",
    operations=["list", "get", "relationships"],
    relationship_types=["children", "parent"],
    work_kinds=["Initiative", "Project", "Issue", "Task"],
    include_archived=False,
)
permissions = [
    dict(operation="devgraph.work." + op, resource=RESOURCE, constraints=work)
    for op in work["operations"]
]
permissions += [
    dict(
        operation="devgraph.arena.list",
        resource=RESOURCE,
        constraints=dict(scope="devgraph.arena.read", all_arenas=True),
    )
]
grants = object.__new__(ArenaMonitorReadEnrollment)._decode_grants(permissions)
app = FastAPI()
register_wallet_monitor(app, storage=storage, enrollment=SimpleNamespace(), audit_log=AuditLog())
with TestClient(app, base_url="https://graph.example") as client:
    response = client.get("/monitor")
    shell = dict(body=response.text, headers=dict(response.headers))
reads = {
    "/monitor/topology/v1": project_monitor_read(storage, grants, "/monitor/topology/v1"),
    "/todos/v2": project_monitor_read(
        storage, grants, "/todos/v2?progress=not_started&archived=exclude&limit=6"
    ),
}
reads["conditional"] = project_monitor_read(
    storage,
    grants,
    "/monitor/topology/v1?if_revision=" + reads["/monitor/topology/v1"]["representation_revision"],
)
print(json.dumps(jsonable_encoder(dict(shell=shell, reads=reads))))
