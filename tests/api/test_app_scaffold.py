"""Commit 1: FastAPI scaffold builds and serves via TestClient.

No running server process and no Neo4j: services are memory-backed and
requests go through the in-process TestClient (decision doc 0018).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from devgraph.api import ApiServices, create_app
from devgraph.auth import (
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    LocalDevVerifier,
)
from devgraph.events.outbox import EventOutbox
from devgraph.model.base import utc_now
from devgraph.model.initiative_observations import InitiativeObservationRepository
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph"
FAKE_CREDENTIAL = "fake-credential-api"


def build_services(
    scopes: frozenset[str] = frozenset(),
    *,
    credential: str = FAKE_CREDENTIAL,
) -> ApiServices:
    storage = MemoryGraphStorage()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(
        credential,
        CredentialEnvelope(
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
            scopes=scopes,
            expires_at=utc_now() + timedelta(hours=1),
            issuer="devgraph-test-issuer",
            audience=AUDIENCE,
        ),
    )
    authorized_graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=AuditLog(),
        repository=WorkObjectRepository(storage),
        initiative_observations=InitiativeObservationRepository(storage),
        monitor_storage=storage,
    )
    return ApiServices(
        authorized_graph=authorized_graph,
        outbox=EventOutbox(storage),
        storage=storage,
        verifier=verifier,
        audience=AUDIENCE,
    )


class TestScaffold:
    def test_app_builds_and_serves_live_without_server_or_neo4j(self) -> None:
        app = create_app(build_services())
        client = TestClient(app)
        response = client.get("/live")
        assert response.status_code == 200
        assert response.json() == {"live": True}

    def test_docs_surfaces_are_disabled(self) -> None:
        app = create_app(build_services())
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404

    def test_ready_fails_closed_without_migration_configuration(self) -> None:
        client = TestClient(create_app(build_services()))

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json() == {
            "applied": [],
            "current_applied_version": 0,
            "manifest_schema_version": 0,
            "maximum_schema_version": 0,
            "minimum_schema_version": 0,
            "ready": False,
            "reason": "migration_configuration_absent",
        }

    def test_live_remains_process_only_when_storage_health_fails(self) -> None:
        class ExplodingStorage(MemoryGraphStorage):
            def health(self):
                raise RuntimeError("raw secret-bearing storage error")

        services = replace(build_services(), storage=ExplodingStorage())
        client = TestClient(create_app(services))

        response = client.get("/live")

        assert response.status_code == 200
        assert response.json() == {"live": True}
