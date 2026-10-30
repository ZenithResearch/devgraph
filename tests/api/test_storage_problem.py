"""Storage faults have safe, machine-readable responses on signed write routes."""

import pytest
from tests.auth.test_secs_work import KEY, b64, client_and_services, proof, request

from devgraph.storage.base import StorageUnavailable


@pytest.mark.parametrize("route", ["/work-operations/v1", "/arena-operations/v1"])
def test_storage_failure_returns_redacted_service_unavailable(monkeypatch, route):
    from tests.api.test_arenas import arena_proof, create_request

    client, services, audit = client_and_services()
    raw = create_request() if route.startswith("/arena") else request()
    projection = arena_proof(raw) if route.startswith("/arena") else proof(raw)

    def unavailable():
        raise StorageUnavailable("secret-bearing database error must never leave the server")

    monkeypatch.setattr(services.storage, "work_mutation_transaction", unavailable)
    response = client.post(route, content=raw, headers={
        "X-Devgraph-Work-Authority": b64(projection), "Idempotency-Key": KEY,
    })
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Storage unavailable"
    assert response.json()["detail"] == ""
    assert "secret-bearing" not in response.text
    assert services.storage.query("EventReceipt") == []
    assert services.storage.query("Arena") == []
    assert services.storage.query("Issue") == []
    assert audit.records == []
