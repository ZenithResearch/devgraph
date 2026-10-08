import json

import pytest
from tests.api.test_app_scaffold import FAKE_CREDENTIAL
from tests.auth.test_secs_work import KEY, b64, client_and_services, proof


def raw(operation="create", version=None, payload=None, kind="Todo"):
    return json.dumps(
        dict(
            schema="devgraph.work-request.v2",
            operation=operation,
            kind=kind,
            id="example",
            expected_version=version,
            payload=payload if payload is not None else {"id": "example", "title": "Daily Todo"},
        )
    ).encode()


def submit(client, data, key=KEY):
    return client.post(
        "/todo-operations/v2",
        content=data,
        headers={"X-Devgraph-Work-Authority": b64(proof(data, key)), "Idempotency-Key": key},
    )


def test_v2_create_progress_archive_restore_receipts_retry_and_version_conflict():
    client, services, audit = client_and_services(frozenset({"devgraph.read"}))
    create = raw()
    first = submit(client, create)
    assert first.status_code == 201, first.text
    assert first.json()["work"]["progress"] == "not_started"
    assert first.json()["work"]["workflow_id"] is None
    replay = submit(client, create)
    assert replay.status_code == 200 and replay.json()["receipt"]["duplicate"]
    assert replay.json()["receipt"]["receipt_id"] == first.json()["receipt"]["receipt_id"]
    assert replay.json()["work"] is None
    done = raw(
        "progress.set",
        1,
        {"progress": "done", "record_id": "complete", "reason": "Called supplier"},
    )
    result = submit(client, done, KEY + "done")
    assert result.status_code == 200, result.text
    assert result.json()["work"]["progress"] == "done"
    conflict = submit(client, raw("archive", 1, {}), KEY + "stale")
    assert conflict.status_code == 412
    archived = submit(client, raw("archive", 2, {}), KEY + "archive")
    assert archived.json()["work"]["archived"] and archived.json()["work"]["progress"] == "done"
    restored = submit(client, raw("restore", 3, {}), KEY + "restore")
    assert not restored.json()["work"]["archived"] and restored.json()["work"]["progress"] == "done"
    assert len(services.storage.query("EventReceipt")) == 4
    headers = {"Authorization": "Bearer " + FAKE_CREDENTIAL}
    page = client.get("/todos/v2?progress=not_started&archived=exclude", headers=headers)
    assert page.status_code == 200 and page.json()["total"] == 0
    assert client.get("/todos/v2?progress=done", headers=headers).json()["total"] == 1
    assert client.get("/todos/v2/Todo/example", headers=headers).json()["progress"] == "done"
    board = client.get("/monitor/kanban/v2", headers=headers)
    assert board.status_code == 200, board.text
    assert next(c for c in board.json()["columns"] if c["id"] == "done")["count"] == 1


@pytest.mark.parametrize("path", ["/todos/v2", "/todos/v2/Todo/example", "/monitor/kanban/v2"])
def test_v2_reads_need_read_authority(path):
    client, _, _ = client_and_services()
    assert client.get(path).status_code in (401, 403)


def test_v1_v2_endpoint_and_authority_domains_cannot_be_interchanged():
    client, services, _ = client_and_services(frozenset({"devgraph.read"}))
    data = raw()
    headers = {"X-Devgraph-Work-Authority": b64(proof(data)), "Idempotency-Key": KEY}
    assert client.post("/work-operations/v1", content=data, headers=headers).status_code == 403
    headers["X-Devgraph-Work-Authority"] = b64(proof(data, operation="devgraph.work.create.v1"))
    assert client.post("/todo-operations/v2", content=data, headers=headers).status_code == 403
    assert (
        client.post(
            "/todo-operations/v2",
            content=data,
            headers={"Authorization": "Bearer " + FAKE_CREDENTIAL, "Idempotency-Key": KEY},
        ).status_code
        == 403
    )
    assert not services.storage.query("Todo")


def test_classification_report_and_invalid_kind_are_bounded_authenticated_reads():
    client, services, _ = client_and_services(frozenset({"devgraph.read"}))
    from tests.model.test_todo_progress import legacy

    from devgraph.model.work import Task

    legacy(services.storage, Task(id="old", title="Legacy Draft"))
    headers = {"Authorization": "Bearer " + FAKE_CREDENTIAL}
    report = client.get("/todos/v2/classification-report", headers=headers)
    assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
    assert report.json()["unresolved"][0]["key"] == "Task/old"
    assert report.json()["mapped"] == {"not_started": 0, "in_progress": 0, "done": 0}
    assert client.get("/todos/v2/classification-report").status_code in (401, 403)
    assert client.get("/todos/v2/Nonsense/old", headers=headers).status_code == 422
