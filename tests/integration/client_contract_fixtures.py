"""Test-only real-stack fixture for the bounded local HTTP client proof."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from devgraph.api import ApiServices, create_app
from devgraph.auth import (
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    LocalDevVerifier,
)
from devgraph.auth.scopes import SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE
from devgraph.client import DevgraphHttpClient, DevgraphRequestContext
from devgraph.events.outbox import EventOutbox
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

_AUDIENCE = "devgraph-client-contract"
_EXPIRES = datetime(2099, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class AuthorityContexts:
    read_only: DevgraphRequestContext
    write_only: DevgraphRequestContext
    full_sequence: DevgraphRequestContext
    wrong_scope: DevgraphRequestContext
    invalid: DevgraphRequestContext
    missing: None = None


class RepositoryDelegateSpy:
    """Test-only counting wrapper around the real work-object repository."""

    def __init__(self, delegate: WorkObjectRepository) -> None:
        self._delegate = delegate
        self.reset_calls()

    def reset_calls(self) -> None:
        self.calls = {
            "get_by_id": 0,
            "query": 0,
            "create": 0,
            "transition_status": 0,
        }

    def get_by_id(self, *args: Any, **kwargs: Any):
        self.calls["get_by_id"] += 1
        return self._delegate.get_by_id(*args, **kwargs)

    def query(self, *args: Any, **kwargs: Any):
        self.calls["query"] += 1
        return self._delegate.query(*args, **kwargs)

    def create(self, *args: Any, **kwargs: Any):
        self.calls["create"] += 1
        return self._delegate.create(*args, **kwargs)

    def transition_status(self, *args: Any, **kwargs: Any):
        self.calls["transition_status"] += 1
        return self._delegate.transition_status(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


@dataclass(frozen=True)
class ObservationHandles:
    storage: MemoryGraphStorage
    repository: RepositoryDelegateSpy
    audit_log: AuditLog
    outbox: EventOutbox


@dataclass(frozen=True)
class ClientContractFixture:
    client: DevgraphHttpClient
    transport: TestClient
    contexts: AuthorityContexts
    observation: ObservationHandles
    scopes_by_context: dict[str, frozenset[str] | None]


def _envelope(name: str, scopes: frozenset[str]) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id=f"synthetic-actor-{name}",
        session_id=f"synthetic-session-{name}",
        correlation_id=f"synthetic-correlation-{name}",
        scopes=scopes,
        expires_at=_EXPIRES,
        issuer="synthetic-client-contract-issuer",
        audience=_AUDIENCE,
    )


def build_client_contract_fixture() -> ClientContractFixture:
    storage = MemoryGraphStorage()
    repository = RepositoryDelegateSpy(WorkObjectRepository(storage))
    audit_log = AuditLog()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    scope_matrix = {
        "read_only": frozenset({SCOPE_READ}),
        "write_only": frozenset({SCOPE_WRITE}),
        "full_sequence": frozenset({SCOPE_READ, SCOPE_WRITE}),
        "wrong_scope": frozenset({SCOPE_ADMIN}),
    }
    markers = {name: f"opaque-client-contract-{name}" for name in scope_matrix}
    for name, scopes in scope_matrix.items():
        verifier.register(markers[name], _envelope(name, scopes))

    contexts = AuthorityContexts(
        read_only=DevgraphRequestContext(credential=markers["read_only"]),
        write_only=DevgraphRequestContext(credential=markers["write_only"]),
        full_sequence=DevgraphRequestContext(credential=markers["full_sequence"]),
        wrong_scope=DevgraphRequestContext(credential=markers["wrong_scope"]),
        invalid=DevgraphRequestContext(credential="opaque-client-contract-invalid"),
    )
    outbox = EventOutbox(storage, receipt_id_factory=_receipt_id_factory())
    authorized_graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=_AUDIENCE,
        audit_log=audit_log,
        repository=repository,
    )
    services = ApiServices(
        authorized_graph=authorized_graph,
        outbox=outbox,
        storage=storage,
        verifier=verifier,
        audience=_AUDIENCE,
    )
    transport = TestClient(create_app(services), raise_server_exceptions=False)
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://testserver",
        timeout=1.0,
    )
    return ClientContractFixture(
        client=client,
        transport=transport,
        contexts=contexts,
        observation=ObservationHandles(
            storage=storage,
            repository=repository,
            audit_log=audit_log,
            outbox=outbox,
        ),
        scopes_by_context={**scope_matrix, "invalid": None, "missing": None},
    )


def _receipt_id_factory():
    next_id = 0

    def make_id() -> str:
        nonlocal next_id
        next_id += 1
        return f"event-receipt-client-contract-{next_id}"

    return make_id
