"""Arena API and strict client boundary; signatures use synthetic fixture keys only."""

import json

import pytest
from tests.api.test_app_scaffold import FAKE_CREDENTIAL
from tests.auth.test_secs_work import KEY, b64, client_and_services, proof, request
from tests.model.test_arena_runtime import arena_command

from devgraph.arena_requests import ArenaRequest


def arena_proof(raw, key=KEY, **overrides):
    parsed = ArenaRequest.from_json(raw)
    return proof(
        request(),
        key,
        operation=parsed.authority_operation,
        resources=list(parsed.resources),
        request_digest_sha256=parsed.digest,
        **overrides,
    )


def post(client, raw, key=KEY, projection=None, path="/arena-operations/v1", headers=None):
    return client.post(
        path,
        content=raw,
        headers={
            "X-Devgraph-Work-Authority": b64(projection or arena_proof(raw, key)),
            "Idempotency-Key": key,
            **(headers or {}),
        },
    )


def create_request():
    return arena_command("create", payload={"id": "gallery", "title": "Gallery"}).canonical


def test_arena_create_retry_and_scoped_read_round_trip():
    from devgraph.client.http import DevgraphHttpClient, DevgraphRequestContext, DevgraphWorkContext

    transport, services, audit = client_and_services(frozenset({"devgraph.read"}))
    client = DevgraphHttpClient(base_url="http://testserver", transport=transport, timeout=5)
    raw = create_request()
    context = DevgraphWorkContext(projection_json=arena_proof(raw))
    first = client.execute_arena(context, request_json=raw, idempotency_key=KEY)
    assert first.arena.id == "gallery" and first.work is None
    assert first.receipt.operation == "devgraph.arena.create.v1"
    again = client.execute_arena(context, request_json=raw, idempotency_key=KEY)
    assert again.arena is None and again.work is None and again.receipt.duplicate
    assert first.receipt.receipt_id == again.receipt.receipt_id
    read = DevgraphRequestContext(credential=FAKE_CREDENTIAL)
    assert client.get_arena(read, arena_id="gallery") == first.arena
    assert client.list_arenas(read).items == (first.arena,)
    assert len(services.storage.query("EventReceipt")) == 1
    assert len(audit.records) == 2


@pytest.mark.parametrize(
    "path", ["/arenas", "/arenas/gallery", "/arenas/gallery/members", "/work/Task/review/arena"]
)
def test_arena_reads_require_authority_before_storage(path, monkeypatch):
    client, services, _ = client_and_services()

    def storage_forbidden(*args, **kwargs):
        pytest.fail("unauthorized Arena read touched storage")

    monkeypatch.setattr(services.storage, "query", storage_forbidden)
    monkeypatch.setattr(services.storage, "get_node", storage_forbidden)
    monkeypatch.setattr(services.storage, "list_edges", storage_forbidden)
    assert client.get(path).status_code == 401


def test_arena_proof_cannot_cross_work_route_or_modify_another_request():
    client, services, _ = client_and_services()
    raw = create_request()
    assert post(client, raw, path="/work-operations/v1").status_code == 403
    changed = json.loads(raw)
    changed["payload"]["title"] = "Changed"
    response = post(client, json.dumps(changed).encode(), projection=arena_proof(raw))
    assert response.status_code == 403
    assert post(client, raw, headers={"Authorization": "Bearer valid-token"}).status_code == 403
    assert services.storage.query("Arena") == []
    assert services.storage.query("EventReceipt") == []


def test_work_proof_and_work_kind_do_not_broaden_into_arenas():
    client, services, _ = client_and_services()
    assert (
        client.post(
            "/arena-operations/v1",
            content=request(),
            headers={
                "X-Devgraph-Work-Authority": b64(proof(request())),
                "Idempotency-Key": KEY,
            },
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/work/Arena/gallery", headers={"Authorization": "Bearer valid-token"}
        ).status_code
        == 422
    )
    assert services.storage.query("Arena") == []


def test_stale_arena_mutation_rolls_back_receipt_and_record():
    client, services, _ = client_and_services()
    assert post(client, create_request()).status_code == 201
    raw = arena_command("patch", payload={"title": "stale"}, version=2).canonical
    assert post(client, raw, key=KEY + "-stale").status_code == 412
    assert services.storage.get_node("Arena", "gallery").properties["title"] == "Gallery"
    assert len(services.storage.query("EventReceipt")) == 1


def test_monitor_projects_arena_separately_with_only_root_membership_edges():
    from tests.model.test_named_work import command, create

    from devgraph.arena_requests import ArenaRequest
    from devgraph.arenas import ArenaMutations
    from devgraph.monitoring import build_monitor_snapshot
    from devgraph.named_work import NamedWorkMutations

    _, services, _ = client_and_services()
    work = NamedWorkMutations(services.storage)
    create(work, "Initiative", "gallery-plan")
    create(work, "Project", "gallery-project")
    work.execute(
        command(
            "parent.set",
            "Project",
            "gallery-project",
            version=1,
            payload={
                "previous_parent": None,
                "parent": {"kind": "Initiative", "id": "gallery-plan", "expected_version": 1},
            },
        )
    )
    arenas = ArenaMutations(services.storage)
    arenas.execute(ArenaRequest.from_json(create_request()))
    arenas.execute(
        arena_command(
            "member.set",
            kind="Initiative",
            subject="gallery-plan",
            version=2,
            payload={
                "previous_arena": None,
                "arena": {"kind": "Arena", "id": "gallery", "expected_version": 1},
            },
        )
    )
    snapshot = build_monitor_snapshot(services.storage)
    arena = next(node for node in snapshot["graph_nodes"] if node["kind"] == "Arena")
    assert arena["category"] == "arena" and arena["status"] == "active"
    assert snapshot["total_work"] == 2
    assert [
        edge for edge in snapshot["graph_edges"] if edge["relationship"] == "CONTAINS_WORK"
    ] == [
        {
            "source": "Arena:gallery",
            "target": "Initiative:gallery-plan",
            "relationship": "CONTAINS_WORK",
        },
    ]
