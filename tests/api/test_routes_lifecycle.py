"""Commit 4: proposal acceptance/conversion write routes.

Writes flow through the idempotent executor over the outbox seam and
the P1-hardened lifecycle behind the authorized façade. Retried
identical requests cannot duplicate side effects; denied writes fail
closed with no receipts and no mutations. All fixtures are synthetic.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue, Proposal


def _client(scopes: frozenset[str]) -> tuple[TestClient, object]:
    services = build_services(scopes)
    services.authorized_graph._repository = WorkObjectRepository(services.storage)
    app = create_app(services)
    return TestClient(app, raise_server_exceptions=False), services


def _seed_proposal(services, proposal_id: str = "proposal-1") -> None:
    proposal = Proposal(id=proposal_id, title="Synthetic proposal", priority=5)
    services.storage.create_node("Proposal", proposal_id, proposal.to_node_properties())


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {FAKE_CREDENTIAL}",
        "Idempotency-Key": key,
    }


class TestAcceptRoute:
    def test_accept_returns_work_and_receipt(self) -> None:
        client, services = _client(frozenset({SCOPE_WRITE}))
        _seed_proposal(services)
        response = client.post(
            "/proposals/proposal-1/accept",
            json={"decision_id": "decision-1", "decision_title": "Approve"},
            headers=_headers("synthetic-key-accept-1"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["work"]["status"] == "accepted"
        assert body["receipt"]["duplicate"] is False
        assert body["receipt"]["operation"] == "accept_proposal"

    def test_retried_accept_does_not_duplicate_side_effects(self) -> None:
        client, services = _client(frozenset({SCOPE_WRITE}))
        _seed_proposal(services)
        payload = {"decision_id": "decision-1", "decision_title": "Approve"}
        headers = _headers("synthetic-key-accept-retry")
        first = client.post("/proposals/proposal-1/accept", json=payload, headers=headers)
        second = client.post("/proposals/proposal-1/accept", json=payload, headers=headers)

        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["receipt"]["duplicate"] is True
        assert second.json()["work"] is None
        assert second.json()["receipt"]["receipt_id"] == first.json()["receipt"]["receipt_id"]
        edges = services.storage.list_edges("ACCEPTED_BY_DECISION")
        assert len(edges) == 1
        receipts = services.storage.query(EVENT_RECEIPT_LABEL)
        assert len(receipts) == 1

    def test_missing_idempotency_key_is_rejected(self) -> None:
        client, services = _client(frozenset({SCOPE_WRITE}))
        _seed_proposal(services)
        response = client.post(
            "/proposals/proposal-1/accept",
            json={"decision_id": "decision-1", "decision_title": "Approve"},
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 422

    def test_denied_write_leaves_no_receipt_or_mutation(self) -> None:
        client, services = _client(frozenset())
        _seed_proposal(services)
        response = client.post(
            "/proposals/proposal-1/accept",
            json={"decision_id": "decision-1", "decision_title": "Approve"},
            headers=_headers("synthetic-key-denied"),
        )
        assert response.status_code == 403
        assert FAKE_CREDENTIAL not in json.dumps(response.json())
        assert services.storage.query(EVENT_RECEIPT_LABEL) == []
        assert services.storage.list_edges("ACCEPTED_BY_DECISION") == []


class TestConvertRoute:
    def test_convert_after_accept_inherits_priority(self) -> None:
        client, services = _client(frozenset({SCOPE_WRITE}))
        _seed_proposal(services)
        client.post(
            "/proposals/proposal-1/accept",
            json={"decision_id": "decision-1", "decision_title": "Approve"},
            headers=_headers("synthetic-key-a"),
        )
        response = client.post(
            "/proposals/proposal-1/convert",
            json={"issue_id": "issue-1"},
            headers=_headers("synthetic-key-c"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["work"]["kind"] == "Issue"
        assert body["work"]["priority"] == 5
        assert body["receipt"]["subject_label"] == "Issue"

    def test_convert_unaccepted_proposal_maps_to_conflict(self) -> None:
        client, services = _client(frozenset({SCOPE_WRITE}))
        _seed_proposal(services)
        response = client.post(
            "/proposals/proposal-1/convert",
            json={"issue_id": "issue-1"},
            headers=_headers("synthetic-key-conflict"),
        )
        assert response.status_code == 409
        assert services.storage.get_node("Issue", "issue-1") is None


def test_archive_and_status_routes_are_idempotent_writes() -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository.create(Issue(id="life-1", title="Life"))
    status = client.post(
        "/work/Issue/life-1/status", json={"status": "review"}, headers=_headers("status-1")
    )
    assert status.status_code == 200 and status.json()["work"]["status"] == "review"
    archived = client.post("/work/Issue/life-1/archive", headers=_headers("archive-1"))
    assert archived.status_code == 200 and archived.json()["work"]["status"] == "archived"
    duplicate = client.post("/work/Issue/life-1/archive", headers=_headers("archive-1"))
    assert duplicate.status_code == 200 and duplicate.json()["work"] is None


def test_archive_is_d0_without_if_match_and_requires_idempotency() -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository.create(Issue(id="d0", title="D0"))
    auth = {"Authorization": f"Bearer {FAKE_CREDENTIAL}", "If-Match": "garbage"}
    assert client.post("/work/Issue/d0/archive", headers=auth).status_code == 422
    response = client.post("/work/Issue/d0/archive", headers={**auth, "Idempotency-Key": "d0-key"})
    assert response.status_code == 200 and response.json()["work"]["status"] == "archived"


def test_denied_archive_and_status_have_zero_effects() -> None:
    client, services = _client(frozenset())
    services.authorized_graph._repository.create(Issue(id="deny", title="Denied"))
    for path, body, key in (("archive", None, "a"), ("status", {"status": "review"}, "s")):
        response = client.post(f"/work/Issue/deny/{path}", json=body, headers=_headers(key))
        assert response.status_code == 403
    node = services.authorized_graph._repository.get_by_id("Issue", "deny")
    assert node.status.value == "draft" and node.version == 1
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []
    assert services.authorized_graph._audit_log.records == []


def test_status_body_is_exact_and_duplicate_does_not_rerun() -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository.create(Issue(id="status", title="Status"))
    assert (
        client.post(
            "/work/Issue/status/status",
            json={"status": "review", "extra": True},
            headers=_headers("extra"),
        ).status_code
        == 422
    )
    first = client.post(
        "/work/Issue/status/status", json={"status": "review"}, headers=_headers("same")
    )
    duplicate = client.post(
        "/work/Issue/status/status", json={"status": "draft"}, headers=_headers("same")
    )
    assert first.status_code == 200 and duplicate.json()["work"] is None
    assert (
        services.authorized_graph._repository.get_by_id("Issue", "status").status.value == "review"
    )


def test_generic_status_cannot_accept_proposal_or_leave_terminal_or_bypass_archive() -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    repo = services.authorized_graph._repository
    repo.create(Proposal(id="p", title="Proposal"))
    accepted = client.post(
        "/work/Proposal/p/status", json={"status": "accepted"}, headers=_headers("p")
    )
    assert accepted.status_code == 409
    repo.create(Issue(id="terminal", title="Terminal"))
    client.post("/work/Issue/terminal/status", json={"status": "accepted"}, headers=_headers("t1"))
    assert (
        client.post(
            "/work/Issue/terminal/status", json={"status": "draft"}, headers=_headers("t2")
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/work/Issue/terminal/status", json={"status": "archived"}, headers=_headers("t3")
        ).status_code
        == 409
    )


@pytest.mark.parametrize("status", ["draft", "review", "accepted", "archived"])
def test_route_accepts_exact_work_status_vocabulary_at_schema_boundary(status) -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository.create(Issue(id=f"vocab-{status}", title="Vocabulary"))
    response = client.post(
        f"/work/Issue/vocab-{status}/status",
        json={"status": status},
        headers=_headers(f"vocab-{status}"),
    )
    assert response.status_code != 422


def test_status_requires_idempotency_key() -> None:
    client, services = _client(frozenset({SCOPE_WRITE}))
    services.authorized_graph._repository.create(Issue(id="missing-key", title="Missing"))
    response = client.post(
        "/work/Issue/missing-key/status",
        json={"status": "review"},
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.status_code == 422
