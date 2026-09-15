import json

import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue


def _client():
    services = build_services(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository = WorkObjectRepository(services.storage)
    original = Issue(
        id="i-1",
        title="Old",
        description="keep",
        priority=7,
        artifact_ids=("a-1",),
        external_link_ids=("l-1",),
    )
    services.authorized_graph._repository.create(original)
    return TestClient(create_app(services), raise_server_exceptions=False), services


def _headers(key: str = "patch-1", if_match: str | None = '"1"') -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {FAKE_CREDENTIAL}",
        "Idempotency-Key": key,
    }
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


@pytest.mark.parametrize(
    "value",
    ["*", 'W/"1"', '"1", "2"', '"1","2"', '"0"', '"-1"', '"abc"', "", "1", ' "1"', '"1" '],
)
def test_if_match_reject_matrix_precedes_all_side_effects(value: str) -> None:
    client, services = _client()
    response = client.patch(
        "/work/Issue/i-1",
        json={"title": "request-content-marker"},
        headers=_headers(if_match=value),
    )
    assert response.status_code == 400
    assert response.json()["title"] == "Invalid version precondition"
    assert "request-content-marker" not in json.dumps(response.json())
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []
    assert services.authorized_graph._audit_log.records == []
    assert services.authorized_graph._repository.get_by_id("Issue", "i-1").title == "Old"


def test_missing_if_match_precedes_all_side_effects() -> None:
    client, services = _client()
    response = client.patch(
        "/work/Issue/i-1", json={"title": "New"}, headers=_headers(if_match=None)
    )
    assert response.status_code == 428
    assert response.json()["title"] == "Precondition required"
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []
    assert services.authorized_graph._audit_log.records == []


@pytest.mark.parametrize(
    "field", ["title", "description", "priority", "artifact_ids", "external_link_ids"]
)
def test_explicit_null_rejects_before_auth_executor_or_audit(field: str) -> None:
    client, services = _client()
    response = client.patch(
        "/work/Issue/i-1",
        json={field: None},
        headers={"Idempotency-Key": "null", "If-Match": '"1"'},
    )
    assert response.status_code == 422
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []
    assert services.authorized_graph._audit_log.records == []
    assert services.authorized_graph._repository.get_by_id("Issue", "i-1").version == 1


def test_stale_patch_has_zero_mutation_receipt_or_success_audit_and_safe_412() -> None:
    client, services = _client()
    before = services.authorized_graph._repository.get_by_id("Issue", "i-1")
    response = client.patch(
        "/work/Issue/i-1",
        json={
            "title": "request-content-marker",
            "description": "changed",
            "priority": 9,
            "artifact_ids": ["a-2"],
            "external_link_ids": ["l-2"],
        },
        headers=_headers(if_match='"2"'),
    )
    assert response.status_code == 412
    assert response.json()["title"] == "Version precondition failed"
    assert "request-content-marker" not in json.dumps(response.json())
    after = services.authorized_graph._repository.get_by_id("Issue", "i-1")
    assert after == before
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []
    assert services.authorized_graph._audit_log.records == []


def test_success_is_write_only_and_duplicate_does_not_rerun() -> None:
    client, services = _client()
    first = client.patch("/work/Issue/i-1", json={"title": "New"}, headers=_headers())
    duplicate = client.patch(
        "/work/Issue/i-1", json={"title": "Should not rerun"}, headers=_headers()
    )
    assert first.status_code == 200 and duplicate.status_code == 200
    assert duplicate.json()["work"] is None
    assert duplicate.json()["receipt"]["duplicate"] is True
    persisted = services.authorized_graph._repository.get_by_id("Issue", "i-1")
    assert persisted.title == "New" and persisted.description == "keep"
    assert persisted.version == 2
    assert len(services.storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert [record.operation for record in services.authorized_graph._audit_log.records] == [
        "update_work_object_content"
    ]
