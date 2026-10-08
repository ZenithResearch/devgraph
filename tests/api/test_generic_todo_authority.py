"""Canonical Todo contract is independent of the generic credential transport version."""

import base64
from dataclasses import replace

from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.api.test_credential_work import HEADERS
from tests.api.test_credential_work import fixture as browser_fixture
from tests.auth.test_secs_work import KEY, canonical
from tests.auth.test_secs_work_v2 import proof_v2
from tests.ops.test_named_work_v2_receiver import fixture

from devgraph.api import create_app
from devgraph.client import DevgraphHttpClient, DevgraphWorkV2Context
from devgraph.work_requests import WorkRequest


def raw(operation="create", kind="Todo", version=None, payload=None):
    return WorkRequest.from_json(
        canonical(
            {
                "schema": "devgraph.work-request.v2",
                "operation": operation,
                "kind": kind,
                "id": "generic-todo",
                "expected_version": version,
                "payload": payload
                if payload is not None
                else {"id": "generic-todo", "title": "Generic Todo"},
            }
        )
    ).canonical


def headers(request, key=KEY):
    return {
        "Idempotency-Key": key,
        "X-Devgraph-Work-Authority": base64.urlsafe_b64encode(proof_v2(request, key))
        .rstrip(b"=")
        .decode(),
    }


def test_canonical_todo_progress_archive_restore_and_status_only_recovery(tmp_path):
    receiver, storage, *_ = fixture(tmp_path)
    services = replace(build_services(frozenset()), storage=storage, named_work_v2=receiver)
    transport = TestClient(create_app(services))
    client = DevgraphHttpClient(transport=transport, base_url="http://testserver", timeout=10)
    operations = [
        raw(),
        raw(
            "progress.set",
            version=1,
            payload={"progress": "in_progress", "reason": "Starting", "record_id": "start-record"},
        ),
        raw(
            "progress.set",
            version=2,
            payload={
                "progress": "done",
                "reason": "Completed simple Todo",
                "record_id": "done-record",
            },
        ),
        raw("archive", version=3, payload={}),
        raw("restore", version=4, payload={}),
    ]
    for index, request in enumerate(operations):
        key = f"{KEY}-{index}"
        context = DevgraphWorkV2Context(projection_json=proof_v2(request, key))
        absent = client.reconcile_credential_work(
            context, request_json=request, idempotency_key=key
        )
        assert absent.state == "unknown"
        result = client.execute_named_work(context, request_json=request, idempotency_key=key)
        recovered = client.reconcile_credential_work(
            context, request_json=request, idempotency_key=key
        )
        assert recovered.receipt.receipt_id == result.receipt.receipt_id
        assert recovered.receipt.duplicate
    todo = storage.get_node("Todo", "generic-todo")
    assert todo.properties["progress"] == "done"
    assert not todo.archived
    assert todo.properties["version"] == 5


def test_request_domain_never_follows_transport_version(tmp_path):
    receiver, storage, *_ = fixture(tmp_path)
    client = TestClient(
        create_app(replace(build_services(frozenset()), storage=storage, named_work_v2=receiver))
    )
    request = raw()
    for path in [
        "/work-operations/v1",
        "/work-operations/v2",
        "/work-operations/v2/status",
        "/arena-operations/v2",
    ]:
        assert client.post(path, content=request, headers=headers(request)).status_code == 403
    assert (
        client.post(
            "/todo-operations/v2",
            content=request,
            headers={**headers(request), "Authorization": "Bearer read-only"},
        ).status_code
        == 403
    )
    assert not storage.query("Todo")


def test_browser_transport_advertises_and_returns_canonical_todo(monkeypatch, tmp_path):
    client, storage, _, _ = browser_fixture(monkeypatch, tmp_path)
    capabilities = client.post("/credential-work/v2/capabilities", json={}, headers=HEADERS).json()
    assert "devgraph.work.workflow.transition.v2" in capabilities["operations"]
    assert "devgraph.work.progress.set.v2" in capabilities["operations"]
    assert "devgraph.work.status.v2" not in capabilities["operations"]
    request = raw().decode()
    prepared = client.post(
        "/credential-work/v2/prepare",
        json={
            "request": request,
            "idempotency_key": KEY,
            "holder_public_key": "ab" * 32,
            "caller": {"kind": "browser", "id": str(client.base_url).rstrip("/")},
        },
        headers=HEADERS,
    )
    assert prepared.status_code == 200, prepared.text
    value = prepared.json()
    result = client.post(
        "/credential-work/v2/execute",
        json={
            "request": request,
            "idempotency_key": KEY,
            "credential": value["credential"],
            "disclosure": value["disclosure"],
            "presentation": {"caller": {"kind": "browser", "id": str(client.base_url).rstrip("/")}},
        },
        headers=HEADERS,
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["state"] == "committed" and body["schema"] == "devgraph.work-result.v2"
    assert body["work"]["progress"] == "not_started"
    assert "status" not in body["work"]
    assert len(storage.query("Todo")) == 1
