"""Shared v2 crypto with provider-owned current hierarchy and old grant retention."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.auth.test_resource_paths import move
from tests.auth.test_write_scope import hierarchy
from tests.native_read.test_holder_grants import alias, credential_request, host
from tests.native_read.test_signed_authority import NOW

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import checksum
from devgraph.auth.monitor_read import MonitorWorkGrant
from devgraph.auth.selector_grants import SelectorGrant, materialize, permission_v2, permitted
from devgraph.storage.memory import MemoryGraphStorage


def request(owner, grant, **kwargs):
    r = credential_request(owner, grant, **kwargs)
    c = r["credential"]
    c["claims"]["schema"] = "secs.holder-credential.v2"
    c["signature"] = owner.sign(
        b"secs.holder-credential.v2/signature\0" + canonical_json(c["claims"])
    ).hex()
    p = r["proof"]
    p.pop("signature")
    p["schema"] = "secs.holder-presentation.v2"
    p["credential_digest"] = checksum(c)
    p["signature"] = owner.sign(
        b"secs.holder-presentation.v2/signature\0" + canonical_json(p)
    ).hex()
    return r


def test_v2_issue_recover_cross_route_replay_and_revoke(tmp_path):
    h, e, owner, args, registry, _, _ = host(tmp_path)
    h.grant_schema = "secs.holder-grant.v2"
    g = h.issue(**args)["grant"]
    assert h.issue(**args)["grant"] == g
    assert g["claims"]["permissions"][0]["roles"][0]["selector"]["selection"]["mode"] == "objects"
    remote, a = alias(tmp_path / "alias", e, registry)
    r = request(owner, g)
    assert a.verify_request(**r, now=NOW).read_grants
    with pytest.raises(UnauthenticatedError, match="replay"):
        e.verify_request(**r, now=NOW)
    e.revoke("dgrg_" + g["claims"]["grant_id"])
    with pytest.raises(UnauthenticatedError):
        remote.verify_request(**request(owner, g, nonce="77" * 16), now=NOW)


def test_selector_uses_current_placement_and_exact_ids_do_not_expand(tmp_path):
    binary = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
    if not binary:
        pytest.skip("native shared authority required")
    issuer = __import__(
        "cryptography.hazmat.primitives.asymmetric.ed25519", fromlist=["Ed25519PrivateKey"]
    ).Ed25519PrivateKey.generate()
    h = SimpleNamespace(
        service="service:fixture-graph",
        binary=Path(binary),
        binary_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        pin={
            "issuer": "provider",
            "key_id": "fixture",
            "service": "service:fixture-graph",
            "public_key": issuer.public_key().public_bytes_raw().hex(),
        },
    )
    s = MemoryGraphStorage()
    hierarchy(s)
    g = MonitorWorkGrant(
        scope="devgraph.work.read",
        operations=("list", "get"),
        relationship_types=("parent",),
        work_kinds=("Task",),
        include_archived=False,
    )
    p = permission_v2(
        dict(
            operation="devgraph.work.get",
            resource="work",
            constraints=json.loads(canonical_json(g)),
        ),
        h.service,
    )
    selector = p["roles"][0]["selector"]
    selector["selection"] = {
        "mode": "descendants",
        "hierarchy": {
            "relation": "devgraph.containment.v1",
            "roots": [{"kind": "Project", "id": "project-a"}],
            "include_self": True,
        },
    }
    w = SelectorGrant(g, selector, h)
    with s.work_mutation_transaction():
        assert [n.id for n in permitted(s, w, [s.get_node("Task", "child")])] == ["child"]
        move(s)
        assert permitted(s, w, [s.get_node("Task", "child")]) == []
    selector["selection"] = {"mode": "objects", "refs": [{"kind": "Task", "id": "child"}]}
    with s.work_mutation_transaction():
        assert materialize(s, [w])[0].work_ids == ("Task/child",)
    selector["selection"]["within"] = {
        "relation": "devgraph.containment.v1",
        "roots": [{"kind": "Project", "id": "project-a"}],
        "include_self": True,
    }
    with s.work_mutation_transaction():
        assert materialize(s, [w]) == ()


def test_actual_http_read_intersects_saved_scope_and_denies_replay(tmp_path):
    from functools import partial

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.auth.test_write_scope import create

    from devgraph.api.wallet_monitor import register_wallet_monitor
    from devgraph.auth.enforcement import AuditLog

    h, e, owner, args, _, _, _ = host(tmp_path)
    h.grant_schema = "secs.holder-grant.v2"
    grant = h.issue(**args)["grant"]
    e.verify_request = partial(e.verify_request, now=NOW)
    h.current_read = partial(h.current_read, now=NOW)
    storage = MemoryGraphStorage()
    for identifier in ("allowed-1", "allowed-2", "hidden"):
        create(storage, "Task", identifier)
    app = FastAPI()
    register_wallet_monitor(app, storage=storage, enrollment=e, audit_log=AuditLog())
    # Keep public errors closed like the production composition.
    from fastapi.responses import JSONResponse

    @app.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse({"error": "access_denied"}, status_code=401)

    selected = json.loads(json.dumps(grant["claims"]["permissions"]))
    for permission in selected:
        permission["roles"][0]["selector"]["selection"] = {
            "mode": "objects",
            "refs": [{"kind": "Task", "id": "allowed-1"}],
        }
    body = request(owner, grant, permissions=selected)
    body["call"]["parameters"]["path"] = "/todos/v2?limit=100"
    proof = body["proof"]
    proof.pop("signature")
    proof["call_digest"] = checksum(body["call"])
    proof["signature"] = owner.sign(
        b"secs.holder-presentation.v2/signature\0" + canonical_json(proof)
    ).hex()
    with TestClient(app) as client:
        response = client.post("/monitor/wallet-access/v1/requests", json=body)
        assert response.status_code == 200, response.text
        assert [r["key"] for r in response.json()["items"]] == ["Task/allowed-1"]
        assert client.post("/monitor/wallet-access/v1/requests", json=body).status_code == 401


def test_v2_all_arenas_stays_dynamic_and_fixed_ids_stay_fixed():
    from tests.auth.test_paired_resources import shared_host
    from tests.model.test_arena_runtime import add_arena

    from devgraph.arenas import ArenaMutations
    from devgraph.auth.arena_access import CollectionArenaReadGrant
    from devgraph.auth.delegated_contract import ArenaReadGrant
    from devgraph.auth.monitor_read import project_monitor_read

    h = shared_host()
    storage = MemoryGraphStorage()
    add_arena(ArenaMutations(storage), "first")
    dynamic = CollectionArenaReadGrant(scope="devgraph.arena.read", all_arenas=True)
    fixed = ArenaReadGrant(scope="devgraph.arena.read", arena_ids=["first"])

    def wrap(g):
        p = permission_v2(
            dict(
                operation="devgraph.arena.list",
                resource="work",
                constraints=json.loads(canonical_json(g)),
            ),
            h.service,
        )
        return SelectorGrant(g, p["roles"][0]["selector"], h)

    both = [wrap(dynamic), wrap(fixed)]
    add_arena(ArenaMutations(storage), "later")
    with storage.work_mutation_transaction():
        views = [materialize(storage, [g]) for g in both]
        assert [x["id"] for x in project_monitor_read(storage, views[0], "/arenas")["items"]] == [
            "first",
            "later",
        ]
        assert [x["id"] for x in project_monitor_read(storage, views[1], "/arenas")["items"]] == [
            "first"
        ]


def test_result_capture_fences_registry_and_rechecks_expiry(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    import devgraph.auth.holder_grants as adapter

    h, e, owner, args, _, _, _ = host(tmp_path)
    h.grant_schema = "secs.holder-grant.v2"
    grant = h.issue(**args)["grant"]
    body = request(owner, grant)
    e.verify_request(**body, now=NOW)
    policy_path = tmp_path / "provisioning.sqlite"

    @contextmanager
    def database():
        db = sqlite3.connect(policy_path, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    with database() as db:
        db.executescript(
            "CREATE TABLE access_assignments(owner,device,request_id,active); "
            "CREATE TABLE access_provisioning_audit(request_id,operation,action_id); "
            "CREATE TABLE access_provisioning_head(id,revision);"
        )
        with e._using_registry():
            db.execute(
                "INSERT INTO access_provisioning_head VALUES(1,?)", (e._registry().revision,)
            )
    e.provisioning = SimpleNamespace(inbox=SimpleNamespace(path=policy_path, _database=database))
    clock = [int(NOW.timestamp())]
    monkeypatch.setattr(adapter, "_seconds", lambda _: clock[0])
    with pytest.raises(UnauthenticatedError, match="expired"):
        with h.current_read(body["credential"], body["proof"]):
            # Both the canonical root registry and assignment database are held.
            for path, statement in [
                (e.path, "UPDATE holder_roots SET status='revoked'"),
                (policy_path, "UPDATE access_provisioning_head SET revision=revision+1"),
            ]:
                db = sqlite3.connect(path, isolation_level=None)
                try:
                    db.execute("PRAGMA busy_timeout=1")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(statement)
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        db.execute("COMMIT")
                    db.execute("ROLLBACK")
                finally:
                    db.close()
            clock[0] += 61
