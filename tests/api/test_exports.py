import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import (
    SCOPE_EXPORT_INTERNAL,
    SCOPE_EXPORT_REDACTED,
    SCOPE_READ,
    SCOPE_WRITE,
)
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue


def _client(scopes: frozenset[str]):
    services = build_services(scopes)
    services.authorized_graph._repository = WorkObjectRepository(services.storage)
    return TestClient(create_app(services), raise_server_exceptions=False), services


def test_three_export_routes_preserve_order_duplicates_and_empty() -> None:
    client, services = _client(frozenset({SCOPE_EXPORT_INTERNAL, SCOPE_EXPORT_REDACTED}))
    services.authorized_graph._repository.create(Issue(id="i-1", title="One"))
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}
    internal = client.post(
        "/exports/internal", json={"kind": "Issue", "ids": ["i-1", "i-1"]}, headers=headers
    )
    assert internal.status_code == 200
    assert [record["id"] for record in internal.json()["records"]] == ["i-1", "i-1"]
    redacted = client.post("/exports/redacted", json={"kind": "Issue", "ids": []}, headers=headers)
    assert redacted.json() == {"mode": "redacted", "records": [], "omitted_private": 0}
    summary = client.post(
        "/exports/public-safe-summary", json={"kind": "Issue", "ids": []}, headers=headers
    )
    assert summary.json()["total"] == 0


def test_missing_export_id_is_all_or_nothing() -> None:
    client, services = _client(frozenset({SCOPE_EXPORT_INTERNAL}))
    services.authorized_graph._repository.create(Issue(id="i-1", title="One"))
    response = client.post(
        "/exports/internal",
        json={"kind": "Issue", "ids": ["i-1", "missing"]},
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path, allowed",
    [
        ("internal", SCOPE_EXPORT_INTERNAL),
        ("redacted", SCOPE_EXPORT_REDACTED),
        ("public-safe-summary", SCOPE_EXPORT_REDACTED),
    ],
)
def test_export_routes_require_mode_exact_scope_and_read_is_insufficient(path, allowed) -> None:
    for scope in (
        SCOPE_READ,
        SCOPE_WRITE,
        SCOPE_EXPORT_REDACTED if allowed == SCOPE_EXPORT_INTERNAL else SCOPE_EXPORT_INTERNAL,
    ):
        client, services = _client(frozenset({scope}))
        response = client.post(
            f"/exports/{path}",
            json={"kind": "Issue", "ids": []},
            headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
        )
        assert response.status_code == 403
        assert services.authorized_graph._audit_log.records == []


@pytest.mark.parametrize("path", ["internal", "redacted", "public-safe-summary"])
def test_all_export_modes_missing_id_are_all_or_nothing_without_audit(path) -> None:
    scope = SCOPE_EXPORT_INTERNAL if path == "internal" else SCOPE_EXPORT_REDACTED
    client, services = _client(frozenset({scope}))
    services.authorized_graph._repository.create(Issue(id="exists", title="Exists"))
    response = client.post(
        f"/exports/{path}",
        json={"kind": "Issue", "ids": ["exists", "missing"]},
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.status_code == 404
    assert services.authorized_graph._audit_log.records == []
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []


def test_export_body_is_strict_and_exports_ignore_idempotency_without_receipt() -> None:
    client, services = _client(frozenset({SCOPE_EXPORT_INTERNAL}))
    headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}", "Idempotency-Key": "ignored"}
    assert (
        client.post(
            "/exports/internal", json={"kind": "Issue", "ids": [], "extra": True}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/exports/internal", json={"kind": "Issue", "ids": []}, headers=headers
        ).status_code
        == 200
    )
    assert services.storage.query(EVENT_RECEIPT_LABEL) == []


def test_summary_preserves_duplicate_counts() -> None:
    client, services = _client(frozenset({SCOPE_EXPORT_REDACTED}))
    services.authorized_graph._repository.create(Issue(id="i", title="Issue"))
    response = client.post(
        "/exports/public-safe-summary",
        json={"kind": "Issue", "ids": ["i", "i"]},
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.json() == {
        "mode": "public_safe_summary",
        "total": 2,
        "by_kind": {"Issue": 2},
        "private_records": 0,
    }


@pytest.mark.parametrize(
    "path, scope",
    [
        ("internal", SCOPE_EXPORT_INTERNAL),
        ("redacted", SCOPE_EXPORT_REDACTED),
        ("public-safe-summary", SCOPE_EXPORT_REDACTED),
    ],
)
def test_each_export_mode_succeeds_with_exact_scope(path, scope) -> None:
    client, _ = _client(frozenset({scope}))
    response = client.post(
        f"/exports/{path}",
        json={"kind": "Issue", "ids": []},
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )
    assert response.status_code == 200
