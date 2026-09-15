from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services
from tests.client.test_http_client import RecordingTransport, Response

from devgraph.api import create_app
from devgraph.client import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphRequestContext,
)
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Task

BODY = {
    "schema": "devgraph.work-supporting-material.v1",
    "work": {"kind": "Task", "id": "parent", "version": 1},
    "items": [
        {
            "kind": "Artifact",
            "id": "plan",
            "via": [{"field": "artifact_ids", "relationships": [], "requirement_id": None}],
            "via_truncated": False,
            "resolution": "available",
            "issues": [],
            "metadata": {"title": "Plan", "role": "old-role", "archived": False},
        }
    ],
    "next_cursor": None,
    "content_access": "metadata_only",
}
DOCUMENT = {
    "schema": "devgraph.work-document.v1",
    "artifact_id": "plan",
    "state": "readable",
    "message": "Read",
    "media_type": "text/markdown",
    "size_bytes": 6,
    "content": "# Plan",
}
CONTEXT = DevgraphRequestContext(credential=FAKE_CREDENTIAL)


def test_typed_new_methods_preserve_single_request_and_bearer_boundary():
    transport = RecordingTransport([Response(200, BODY), Response(200, DOCUMENT)])
    client = DevgraphHttpClient(timeout=5, base_url="http://testserver", transport=transport)
    result = client.get_supporting_material(
        CONTEXT, kind="Task", work_id="parent", limit=2, after="abc"
    )
    assert result.items[0].metadata["role"] == "old-role"
    document = client.get_work_document(CONTEXT, kind="Task", work_id="parent", artifact_id="plan")
    assert document.content == "# Plan"
    assert [call["url"] for call in transport.calls] == [
        "http://testserver/work/Task/parent/supporting-material?limit=2&after=abc",
        "http://testserver/work/Task/parent/supporting-material/Artifact/plan/document",
    ]
    assert all(
        call["headers"] == {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
        and call["method"] == "GET"
        for call in transport.calls
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update(content_access="raw"),
        lambda body: body["work"].update(id="unrelated"),
        lambda body: body["items"][0]["metadata"].update(password="secret"),
        lambda body: body["items"][0]["metadata"].update(archived="false"),
        lambda body: body["items"][0].update(kind="Secret"),
        lambda body: body.update(items=body["items"] * 101),
    ],
)
def test_client_rejects_malformed_or_overbroad_success(mutation):
    body = deepcopy(BODY)
    mutation(body)
    client = DevgraphHttpClient(
        timeout=5, base_url="http://testserver", transport=RecordingTransport([Response(200, body)])
    )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_supporting_material(CONTEXT, kind="Task", work_id="parent")


def test_new_client_and_real_api_projection_round_trip():
    services = build_services(frozenset({"devgraph.read"}))
    WorkObjectRepository(services.storage).create(
        Task(id="parent", title="Parent", artifact_ids=("plan",))
    )
    services.storage.create_node("Artifact", "plan", {"title": "Plan", "role": "old-role"})
    client = DevgraphHttpClient(
        timeout=5, base_url="http://testserver", transport=TestClient(create_app(services))
    )
    result = client.get_supporting_material(CONTEXT, kind="Task", work_id="parent")
    assert result.model_dump(by_alias=True) == BODY
    document = client.get_work_document(CONTEXT, kind="Task", work_id="parent", artifact_id="plan")
    assert document.state == "metadata_only" and document.content is None
    # The existing strict Work client still decodes its unchanged envelope.
    assert client.get_work(CONTEXT, kind="Task", work_id="parent").artifact_ids == ("plan",)


@pytest.mark.parametrize("content", ["", "# Plan\nVerify the change.", "Plan: café 👋"])
def test_bom_document_round_trips_through_api_and_strict_client(tmp_path, monkeypatch, content):
    root = tmp_path.resolve()
    path = root / "plan.md"
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    monkeypatch.setenv("DEVGRAPH_DOCUMENT_ROOTS", json.dumps([str(root)]))
    services = build_services(frozenset({"devgraph.read"}))
    WorkObjectRepository(services.storage).create(
        Task(id="parent", title="Parent", artifact_ids=("plan",))
    )
    services.storage.create_node("Artifact", "plan", {"title": "Plan", "uri": path.as_uri()})
    client = DevgraphHttpClient(
        timeout=5, base_url="http://testserver", transport=TestClient(create_app(services))
    )

    document = client.get_work_document(CONTEXT, kind="Task", work_id="parent", artifact_id="plan")

    assert document.state == "readable"
    assert document.content == content
    assert document.size_bytes == len(content.encode("utf-8"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["items"][0].update(metadata=None),
        lambda body: body["items"][0]["via"][0].update(field="external_link_ids"),
        lambda body: body["items"][0]["via"][0].update(relationships=["ARBITRARY"]),
        lambda body: body["items"][0].update(id="../../private"),
        lambda body: body.update(items=body["items"] * 2),
    ],
)
def test_client_rejects_inconsistent_item_state_and_provenance(mutation):
    body = deepcopy(BODY)
    mutation(body)
    client = DevgraphHttpClient(
        timeout=5, base_url="http://testserver", transport=RecordingTransport([Response(200, body)])
    )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_supporting_material(CONTEXT, kind="Task", work_id="parent")


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_id": "unrelated"},
        {"state": "missing"},
        {"content": None},
        {"size_bytes": 7},
        {"media_type": "text/html"},
    ],
)
def test_client_rejects_inconsistent_document_envelopes(changes):
    client = DevgraphHttpClient(
        timeout=5,
        base_url="http://testserver",
        transport=RecordingTransport([Response(200, {**DOCUMENT, **changes})]),
    )
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.get_work_document(CONTEXT, kind="Task", work_id="parent", artifact_id="plan")
