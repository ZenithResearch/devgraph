"""Synthetic UI-only fixture; no real credential issuance, identity or grant.

Run with: uvicorn tests.browser.wallet_fixture:app --host 127.0.0.1 --port 18883
Never expose this mock authority outside a disposable loopback test.
"""

import time
from types import SimpleNamespace

from fastapi import FastAPI

from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.monitor_read import EnrollmentPolicyV2, MonitorWorkGrant
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Project, Task, Todo
from devgraph.storage.memory import MemoryGraphStorage

storage = MemoryGraphStorage()
repo = WorkObjectRepository(storage)
for item in [
    Todo(id="one", title="Plan the next release", progress="not_started"),
    Project(id="project", title="Wallet connection"),
    Task(id="task", title="Test the reader"),
]:
    repo.create(item)
storage.create_edge("Project", "project", "HAS_CHILD", "Task", "task")
grant = MonitorWorkGrant(
    scope="devgraph.work.read",
    operations=("list", "get", "relationships"),
    relationship_types=("children", "parent"),
    work_kinds=("Todo", "Project", "Task"),
    include_archived=False,
)
policy = EnrollmentPolicyV2.model_validate(
    {
        "schema": "devgraph.native-read-policy.v2",
        "issuer": "ui-fixture",
        "receiver": "https://fixture.example",
        "lifetime_seconds": 600,
        "grants": [grant],
    }
)


class Fixture:
    def policy(self):
        return policy

    def _authority_policy(self, _):
        return {
            "issuer": "ui-fixture",
            "key_id": "ui-only",
            "public_key": "a" * 64,
            "audience": "devgraph",
        }

    def challenge(self, owner, device):
        return {
            "receiver": policy.receiver,
            "expires_at": int(time.time()) + 120,
            "credential_expires_at": int(time.time()) + 600,
            "details": {"fixture": True},
        }

    def enroll(self, **_):
        return {"schema": "devgraph.native-read-result.v3", "credential": {"fixture": True}}

    def verify_request(self, **_):
        return SimpleNamespace(
            read_grants=(grant,),
            actor_id="ui-fixture",
            session_id="synthetic",
            correlation_id="fixture",
        )


app = FastAPI()
register_wallet_monitor(
    app,
    storage=storage,
    enrollment=Fixture(),
    audit_log=AuditLog(),
    web_wallet_url="https://wallet.example",
)


@app.get("/ui-fixture/v1")
def marker():
    return {"synthetic": True}
