from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from client_contract_fixtures import build_client_contract_fixture
from pydantic import ValidationError

from devgraph.auth.scopes import SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE


def test_fixture_composes_real_app_with_exact_synthetic_authority_matrix() -> None:
    fixture = build_client_contract_fixture()

    assert fixture.contexts.read_only.credential != fixture.contexts.write_only.credential
    assert fixture.scopes_by_context == {
        "read_only": frozenset({SCOPE_READ}),
        "write_only": frozenset({SCOPE_WRITE}),
        "full_sequence": frozenset({SCOPE_READ, SCOPE_WRITE}),
        "wrong_scope": frozenset({SCOPE_ADMIN}),
        "invalid": None,
        "missing": None,
    }
    assert fixture.contexts.missing is None
    with pytest.raises((FrozenInstanceError, TypeError, ValueError, ValidationError)):
        fixture.contexts.read_only.credential = "changed"  # type: ignore[misc]

    assert fixture.transport.get("/live").json() == {"live": True}
    assert fixture.observation.storage.query() == []
    assert fixture.observation.audit_log.records == []


def test_production_client_receives_only_http_transport_and_bounded_configuration() -> None:
    fixture = build_client_contract_fixture()
    client_attributes = vars(fixture.client)

    assert set(client_attributes) == {"_transport", "_base_url", "_timeout"}
    assert client_attributes["_transport"] is fixture.transport
    assert all(
        value is not fixture.observation.storage
        and value is not fixture.observation.repository
        and value is not fixture.observation.audit_log
        and value is not fixture.observation.outbox
        for value in client_attributes.values()
    )


def test_fixture_uses_direct_local_verifier_construction_without_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        "devgraph.auth.verifier.LocalDevVerifier.from_env",
        lambda: pytest.fail("fixture must not read verifier mode from the environment"),
    )
    fixture = build_client_contract_fixture()
    assert fixture.contexts.full_sequence.credential
