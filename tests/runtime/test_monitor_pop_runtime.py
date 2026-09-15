from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from devgraph.ops.local_path_integrity import OWNERSHIP_DISABLED_DIAGNOSTIC
from devgraph.ops.secs_monitor_view_read_receiver import LocalSecSMonitorViewReadError
from devgraph.runtime import (
    DATA_ROOT_VARIABLE,
    RuntimeConfigurationError,
    build_production_services,
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
    monkeypatch.delenv(DATA_ROOT_VARIABLE, raising=False)


def test_missing_data_root_keeps_exact_monitor_receiver_absent(monkeypatch) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    loader = Mock(side_effect=AssertionError("loader must not run"))
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    monkeypatch.setattr(
        "devgraph.runtime.load_local_secs_monitor_view_read_adapter",
        loader,
    )

    services = build_production_services()

    assert services.monitor_view_read is None
    loader.assert_not_called()


def test_configured_data_root_loads_fixed_receiver_with_shared_storage_and_audit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    exact_receiver = object()
    loader = Mock(return_value=exact_receiver)
    monkeypatch.setenv(DATA_ROOT_VARIABLE, str(tmp_path))
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    monkeypatch.setattr(
        "devgraph.runtime.load_local_secs_monitor_view_read_adapter",
        loader,
    )

    services = build_production_services()

    assert services.monitor_view_read is exact_receiver
    loader.assert_called_once()
    call = loader.call_args.kwargs
    assert call["data_root"] == tmp_path
    assert call["storage"] is storage
    assert call["audit_log"] is services.authorized_graph._audit_log


def test_relative_or_malformed_receiver_configuration_fails_startup_safely(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    monkeypatch.setenv(DATA_ROOT_VARIABLE, "relative-data")
    with pytest.raises(RuntimeConfigurationError, match="data root"):
        build_production_services()

    monkeypatch.setenv(DATA_ROOT_VARIABLE, str(tmp_path))
    monkeypatch.setattr(
        "devgraph.runtime.load_local_secs_monitor_view_read_adapter",
        Mock(side_effect=LocalSecSMonitorViewReadError("synthetic-secret-marker")),
    )
    with pytest.raises(RuntimeConfigurationError) as denied:
        build_production_services()
    assert str(denied.value) == "monitor receiver configuration is invalid"
    assert "synthetic-secret-marker" not in str(denied.value)


def test_ownership_disabled_mount_has_fixed_operator_diagnostic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _production_environment(monkeypatch)
    storage = StubNeo4jStorage()
    monkeypatch.setenv(DATA_ROOT_VARIABLE, str(tmp_path))
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    monkeypatch.setattr(
        "devgraph.runtime.load_local_secs_monitor_view_read_adapter",
        Mock(
            side_effect=LocalSecSMonitorViewReadError(
                OWNERSHIP_DISABLED_DIAGNOSTIC
            )
        ),
    )

    with pytest.raises(RuntimeConfigurationError) as denied:
        build_production_services()

    assert str(denied.value) == OWNERSHIP_DISABLED_DIAGNOSTIC
