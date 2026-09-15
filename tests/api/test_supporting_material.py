from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.model.base import utc_now
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import AcceptanceCriterion, Requirement, Task

AUTH = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
URL = "/work/Task/parent/supporting-material"


def setup(scopes=frozenset({"devgraph.read"}), **work_fields):
    services = build_services(scopes)
    WorkObjectRepository(services.storage).create(Task(id="parent", title="Parent", **work_fields))
    return services, TestClient(create_app(services))


def get(client, **params):
    return client.get(URL, headers=AUTH, params=params)


def test_all_four_types_fields_edges_dedup_one_hop_and_no_mutations():
    services, client = setup(artifact_ids=("artifact", "missing"), external_link_ids=("link",))
    storage = services.storage
    storage.create_node(
        "Artifact",
        "artifact",
        {
            "title": "Plan",
            "role": "legacy-document",
            "description": "Authored plan",
            "uri": "https://example.test/plan",
            "password": "never-return",
            "raw_payload": {"private": "never-return"},
        },
    )
    storage.create_node(
        "ExternalLink",
        "link",
        {
            "title": "Tracker",
            "role": "generic_url",
            "url": "https://example.test/work",
            "external_id": "123",
        },
    )
    storage.create_node("Artifact", "edge-only", {"title": "Evidence", "summary": "evidence"})
    storage.create_edge("Task", "parent", "HAS_ARTIFACT", "Artifact", "artifact")
    storage.create_edge("Task", "parent", "HAS_ARTIFACT", "Artifact", "edge-only")
    for work in (
        Requirement(id="req", title="Requirement", description="Required behavior"),
        AcceptanceCriterion(id="criterion", title="Criterion", description="Check behavior"),
        AcceptanceCriterion(id="unrelated", title="Not reachable"),
    ):
        WorkObjectRepository(storage).create(work)
    storage.create_edge("Task", "parent", "HAS_REQUIREMENT", "Requirement", "req")
    storage.create_edge(
        "Task", "parent", "HAS_ACCEPTANCE_CRITERION", "AcceptanceCriterion", "criterion"
    )
    storage.create_edge(
        "Requirement", "req", "HAS_ACCEPTANCE_CRITERION", "AcceptanceCriterion", "criterion"
    )
    storage.create_edge(
        "AcceptanceCriterion",
        "criterion",
        "HAS_ACCEPTANCE_CRITERION",
        "AcceptanceCriterion",
        "unrelated",
    )
    storage.archive_node("Artifact", "edge-only")
    before_nodes, before_edges = storage.query(), storage.list_edges()
    response = get(client)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["schema"] == "devgraph.work-supporting-material.v1"
    assert body["content_access"] == "metadata_only" and body["next_cursor"] is None
    assert body["work"] == {"kind": "Task", "id": "parent", "version": 1}
    items = {(item["kind"], item["id"]): item for item in body["items"]}
    assert set(items) == {
        ("Artifact", "artifact"),
        ("Artifact", "edge-only"),
        ("Artifact", "missing"),
        ("ExternalLink", "link"),
        ("Requirement", "req"),
        ("AcceptanceCriterion", "criterion"),
    }
    assert len(items["Artifact", "artifact"]["via"]) == 2
    assert items["Artifact", "artifact"]["metadata"]["role"] == "legacy-document"
    assert items["Artifact", "edge-only"]["metadata"]["archived"] is True
    assert items["Artifact", "missing"]["resolution"] == "missing"
    assert items["Artifact", "missing"]["metadata"] is None
    criterion = items["AcceptanceCriterion", "criterion"]
    assert criterion["metadata"]["description"] == "Check behavior"
    assert {tuple(via["relationships"]) for via in criterion["via"]} == {
        ("HAS_ACCEPTANCE_CRITERION",),
        ("HAS_REQUIREMENT", "HAS_ACCEPTANCE_CRITERION"),
    }
    assert next(via for via in criterion["via"] if via["requirement_id"])["requirement_id"] == "req"
    assert "never-return" not in response.text and "raw_payload" not in response.text
    assert storage.query() == before_nodes and storage.list_edges() == before_edges
    assert storage.query("EventReceipt") == []
    assert [entry.operation for entry in services.authorized_graph._audit_log.records] == [
        "supporting_material"
    ]


def test_empty_missing_parent_and_public_work_kinds_unchanged():
    _, client = setup()
    assert get(client).json()["items"] == []
    assert client.get("/work/Task/missing/supporting-material", headers=AUTH).status_code == 404
    for kind in ("Artifact", "ExternalLink", "Requirement", "AcceptanceCriterion", "Plan"):
        assert (
            client.get(f"/work/{kind}/parent/supporting-material", headers=AUTH).status_code == 422
        )
        assert client.get(f"/work/{kind}/parent", headers=AUTH).status_code == 422


@pytest.mark.parametrize(
    "scopes,headers,status",
    [
        (frozenset(), AUTH, 403),
        (frozenset({"devgraph.write"}), AUTH, 403),
        (frozenset({"devgraph.admin"}), AUTH, 403),
        (frozenset({"devgraph.read"}), {}, 401),
        (frozenset({"devgraph.read"}), {"Authorization": "Bearer invalid"}, 401),
    ],
)
def test_authorization_happens_before_any_storage(scopes, headers, status):
    services, client = setup(scopes)
    services.storage.get_node = Mock(side_effect=AssertionError("unauthorized read"))
    services.storage.supporting_material_references = Mock(side_effect=AssertionError("read"))
    for suffix in ("", "/Artifact/arbitrary/document"):
        assert client.get(URL + suffix, headers=headers).status_code == status
    services.storage.get_node.assert_not_called()
    services.storage.supporting_material_references.assert_not_called()
    assert services.authorized_graph._audit_log.records == []


def test_expired_reader_rejected_before_storage():
    services, client = setup()
    # Replace only this isolated synthetic verifier registration.
    envelope = services.verifier.verify(FAKE_CREDENTIAL, audience=services.audience).envelope
    services.verifier.register(
        FAKE_CREDENTIAL, replace(envelope, expires_at=utc_now() - timedelta(seconds=1))
    )
    services.storage.get_node = Mock(side_effect=AssertionError("expired read"))
    assert get(client).status_code == 401
    services.storage.get_node.assert_not_called()


def test_keyset_pagination_bounds_and_parent_query_binding():
    ids = tuple(f"artifact-{index:03}" for index in range(107))
    services, client = setup(artifact_ids=ids)
    cursor = None
    seen = []
    first_cursor = None
    while True:
        response = get(client, **({"limit": 40, "after": cursor} if cursor else {"limit": 40}))
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 40
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        first_cursor = first_cursor or cursor
        if cursor is None:
            break
    assert seen == list(ids)
    for bad_limit in (0, 101, -1):
        assert get(client, limit=bad_limit).status_code == 422
    assert get(client, limit=39, after=first_cursor).status_code == 400
    for after in ("not-json", "!bad", "x" * 4097):
        assert get(client, limit=40, after=after).status_code in (400, 422)
    other = Task(id="other", title="Other")
    WorkObjectRepository(services.storage).create(other)
    assert (
        client.get(
            "/work/Task/other/supporting-material",
            headers=AUTH,
            params={"limit": 40, "after": first_cursor},
        ).status_code
        == 400
    )
    node = services.storage.get_node("Task", "parent")
    services.storage.update_node(
        "Task", "parent", {**node.properties, "version": 2, "artifact_ids": list(ids[1:])}
    )
    response = get(client, limit=40, after=first_cursor)
    assert response.status_code == 409
    assert response.json()["detail"] == "supporting_material_changed_restart_pagination"


def test_cursor_cannot_substitute_an_unrelated_target():
    services, client = setup(artifact_ids=("a", "b"))
    services.storage.create_node("Artifact", "unrelated", {"title": "Not attached"})
    cursor = get(client, limit=1).json()["next_cursor"]
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    decoded["last"] = "Artifact/unrelated"
    forged = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
    assert get(client, limit=1, after=forged).json()["items"] == []
    assert client.get(URL + "/Artifact/unrelated/document", headers=AUTH).status_code == 404


def test_malformed_legacy_observation_and_safe_field_bounds():
    services, client = setup(artifact_ids=("bad", "observation", "large", "unsafe"))
    storage = services.storage
    storage.create_node("Artifact", "bad", {"title": [], "summary": "valid partial summary"})
    storage.create_node(
        "Artifact",
        "observation",
        {
            "title": "Inference",
            "role": "initiative_observation",
            "problem": "Use typed observation route",
            "confidence": 0.8,
        },
    )
    storage.create_node(
        "Artifact",
        "large",
        {
            "title": "Large",
            "description": "x" * 70000,
            "summary": "Bearer abc123 password=private",
            "role": "old-role",
        },
    )
    storage.create_node("Artifact", "unsafe", {"title": "Unsafe", "uri": "javascript:alert(1)"})
    storage.create_node("Requirement", "bad-req", {"title": "Missing lifecycle", "priority": True})
    storage.create_edge("Task", "parent", "HAS_REQUIREMENT", "Requirement", "bad-req")
    response = get(client)
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items["bad"]["resolution"] == "malformed"
    assert items["bad"]["metadata"]["summary"] == "valid partial summary"
    assert items["bad-req"]["resolution"] == "malformed"
    assert "priority_invalid" in items["bad-req"]["issues"]
    assert items["observation"]["resolution"] == "unsupported_profile"
    assert items["observation"]["metadata"] is None
    assert len(items["large"]["metadata"]["description"]) == 65536
    assert "description_truncated" in items["large"]["issues"]
    assert "abc123" not in response.text and "private" not in response.text
    assert "uri" not in items["unsafe"]["metadata"]
    assert "uri_unsafe" in items["unsafe"]["issues"]


def test_read_scope_does_not_gain_export_authority():
    _, client = setup()
    assert get(client).status_code == 200
    for mode in ("internal", "redacted", "public-safe-summary"):
        assert (
            client.post(
                f"/exports/{mode}", json={"kind": "Task", "ids": ["parent"]}, headers=AUTH
            ).status_code
            == 403
        )


def test_linked_document_resolution_and_unrelated_rejection(tmp_path, monkeypatch):
    # Resolve only the fixture root; the reader traverses descriptors without symlinks.
    document_root = tmp_path.resolve()
    document = document_root / "plan.md"
    document.write_text("# Authored plan\n\nDo the work.")
    monkeypatch.setenv("DEVGRAPH_DOCUMENT_ROOTS", json.dumps([str(document_root)]))
    services, client = setup(artifact_ids=("plan", "missing", "bad"))
    services.storage.create_node(
        "Artifact",
        "plan",
        {"title": "Plan", "uri": document.as_uri(), "media_type": "text/markdown"},
    )
    services.storage.create_node("Artifact", "bad", {"title": []})
    response = client.get(URL + "/Artifact/plan/document", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["state"] == "readable"
    assert response.json()["content"] == "# Authored plan\n\nDo the work."
    assert client.get(URL + "/Artifact/missing/document", headers=AUTH).json()["state"] == "missing"
    assert client.get(URL + "/Artifact/bad/document", headers=AUTH).json()["state"] == "unavailable"
    assert client.get(URL + "/Artifact/not-attached/document", headers=AUTH).status_code == 404
    assert services.storage.query("EventReceipt") == []


def test_edge_only_changes_use_documented_keyset_semantics_and_first_page_refresh():
    services, client = setup()
    storage = services.storage
    for item_id in ("a", "b", "c", "d"):
        storage.create_node("Artifact", item_id, {"title": item_id})
    for item_id in ("b", "d"):
        storage.create_edge("Task", "parent", "HAS_ARTIFACT", "Artifact", item_id)
    page = get(client, limit=1).json()
    assert [item["id"] for item in page["items"]] == ["b"]
    for item_id in ("a", "c"):
        storage.create_edge("Task", "parent", "HAS_ARTIFACT", "Artifact", item_id)
    continued = get(client, limit=1, after=page["next_cursor"])
    assert continued.status_code == 200
    assert [item["id"] for item in continued.json()["items"]] == ["c"]
    # No global graph revision: a new earlier key is found by restarting the list.
    assert [item["id"] for item in get(client).json()["items"]] == ["a", "b", "c", "d"]
