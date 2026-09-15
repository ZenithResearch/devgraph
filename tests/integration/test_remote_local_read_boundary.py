"""Production authority composition with temporary credentials and memory storage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from devgraph.api import create_app
from devgraph.auth import LocalReadCredentialVerifier
from devgraph.client import DevgraphHttpClient
from devgraph.events.outbox import EMITTED_EVENT, EVENT_RECEIPT_LABEL
from devgraph.local_host import (
    LocalHostConfig,
    provision_local_read_credential,
    read_local_read_credential,
)
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Issue
from devgraph.remote import RemoteGateway, TransportSafeProjector
from devgraph.runtime import build_production_services
from devgraph.storage.memory import MemoryGraphStorage


@pytest.mark.parametrize("operation", ["create_issue", "transition_issue_to_review"])
def test_production_read_credential_cannot_authorize_remote_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    config = LocalHostConfig.build(
        data_root=data_root, host_root=tmp_path / "host", validate_data_root=False
    )
    provision_local_read_credential(config)
    credential = read_local_read_credential(config)
    for name, value in {
        "DEVGRAPH_ENVIRONMENT": "production",
        "DEVGRAPH_AUTH_MODE": "local-read",
        "DEVGRAPH_AUDIENCE": "devgraph",
        "DEVGRAPH_DATA_ROOT": str(data_root),
        "NEO4J_URI": "bolt://neo4j.invalid:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "test-only-password",
    }.items():
        monkeypatch.setenv(name, value)
    storage = MemoryGraphStorage()
    monkeypatch.setattr("devgraph.runtime.Neo4jGraphStorage", Mock(return_value=storage))
    services = build_production_services()
    assert isinstance(services.verifier, LocalReadCredentialVerifier)
    repository = WorkObjectRepository(storage)
    repository.create(Issue(id="remote-read-only", title="Synthetic seed"))

    with TestClient(create_app(services)) as transport:
        gateway = RemoteGateway(
            client=DevgraphHttpClient(
                transport=transport, base_url="http://testserver", timeout=1.0
            ),
            projector=TransportSafeProjector(),
        )
        read = {"operation": "get_issue", "arguments": {"work_id": "remote-read-only"}}
        assert gateway.dispatch(read, credential="").reason_code == "credential_required"
        assert gateway.dispatch(read, credential=credential).outcome == "found"
        audit_before = list(services.authorized_graph._audit_log.records)
        work_before = storage.query("Issue")
        arguments = {
            "work_id": "remote-new-issue" if operation == "create_issue" else "remote-read-only",
            "idempotency_key": "synthetic-denied-write",
        }
        if operation == "create_issue":
            arguments["title"] = "Must not be stored"
        response = gateway.dispatch(
            {"operation": operation, "arguments": arguments}, credential=credential
        )

    assert response.reason_code == "forbidden"
    assert response.status == 403
    assert credential not in response.model_dump_json()
    assert storage.query("Issue") == work_before
    assert storage.query(EVENT_RECEIPT_LABEL) == []
    assert storage.list_edges(EMITTED_EVENT) == []
    assert services.authorized_graph._audit_log.records == audit_before
