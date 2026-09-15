from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from devgraph.api import create_app
from devgraph.auth import LocalReadCredentialVerifier
from devgraph.local_host import (
    LocalHostConfig,
    provision_local_read_credential,
    read_local_read_credential,
)
from devgraph.runtime import (
    RuntimeConfigurationError,
    build_production_services,
    create_production_app,
)
from devgraph.storage.base import HealthStatus


class StubNeo4jStorage:
    def health(self) -> HealthStatus:
        return HealthStatus(live=True, ready=False, detail="stub")

    def close(self) -> None:
        pass


def _production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVGRAPH_ENVIRONMENT", "production")
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "fail-closed")
    monkeypatch.setenv("DEVGRAPH_AUDIENCE", "devgraph")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.invalid:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-only-password")


def test_production_runtime_rejects_local_dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_environment(monkeypatch)
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "local-dev")

    with pytest.raises(RuntimeConfigurationError, match="unsupported production auth mode"):
        build_production_services()


def test_production_runtime_retains_legacy_fail_closed_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_environment(monkeypatch)
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "disabled")
    storage = StubNeo4jStorage()
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))

    services = build_production_services()

    assert services.storage is storage


def test_production_runtime_loads_the_local_read_verifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _production_environment(monkeypatch)
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    config = LocalHostConfig.build(
        data_root=data_root,
        host_root=tmp_path / "host",
        validate_data_root=False,
    )
    provision_local_read_credential(config)
    credential = read_local_read_credential(config)
    monkeypatch.setenv("DEVGRAPH_AUTH_MODE", "local-read")
    monkeypatch.setenv("DEVGRAPH_DATA_ROOT", str(data_root))
    storage = StubNeo4jStorage()
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))

    services = build_production_services()

    assert isinstance(services.verifier, LocalReadCredentialVerifier)
    assert services.verifier.verify(credential, audience="devgraph").scopes == frozenset(
        {"devgraph.read"}
    )
    with TestClient(create_app(services)) as client:
        anonymous = client.get("/work/Issue")
        read_credential_write = client.post(
            "/work/Issue",
            json={"id": "read-cannot-write", "title": "Denied"},
            headers={
                "Authorization": f"Bearer {credential}",
                "Idempotency-Key": "read-cannot-write",
            },
        )

    assert anonymous.status_code == 401
    assert read_credential_write.status_code == 403
    assert services.authorized_graph._audit_log.records == []


def test_production_runtime_requires_explicit_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_environment(monkeypatch)
    monkeypatch.delenv("DEVGRAPH_ENVIRONMENT")

    with pytest.raises(RuntimeConfigurationError, match="DEVGRAPH_ENVIRONMENT"):
        build_production_services()


def test_production_app_is_live_but_protected_calls_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))

    with TestClient(create_production_app()) as client:
        assert client.get("/live").json() == {"live": True}
        frontend = client.get("/")
        monitor = client.get("/monitor/snapshot")
        response = client.get("/work/Issue/issue-prod-check")

    assert frontend.status_code == 200
    assert "Dev Graph Monitor" in frontend.text
    assert monitor.status_code == 401
    assert monitor.json()["title"] == "Unauthenticated"
    assert response.status_code == 401
    assert response.json()["title"] == "Unauthenticated"
    assert response.json()["detail"] == "credential verification unavailable in this mode"


@pytest.mark.parametrize("storage_close_fails", [False, True])
@pytest.mark.parametrize("has_cypher_read", [False, True])
def test_production_lifespan_closes_resources_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    storage_close_fails: bool,
    has_cypher_read: bool,
) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    storage.close = Mock(
        side_effect=RuntimeError("storage cleanup failed") if storage_close_fails else None
    )
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    cypher_read = Mock() if has_cypher_read else None
    services = replace(build_production_services(), cypher_read=cypher_read)
    monkeypatch.setattr("devgraph.runtime.build_production_services", lambda: services)
    expected = (
        pytest.raises(RuntimeError, match="storage cleanup failed")
        if storage_close_fails else nullcontext()
    )

    with expected, TestClient(create_production_app()) as client:
        assert client.get("/live").json() == {"live": True}
        storage.close.assert_not_called()
        if cypher_read is not None:
            cypher_read.close.assert_not_called()

    storage.close.assert_called_once_with()
    if cypher_read is not None:
        cypher_read.close.assert_called_once_with()
