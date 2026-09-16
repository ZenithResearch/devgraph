"""Synthetic in-process fixtures for the generic remote authority proof."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from client_contract_fixtures import ObservationHandles, RepositoryDelegateSpy
from fastapi.testclient import TestClient

from devgraph.api import ApiServices, create_app
from devgraph.auth import AuditLog, AuthorizedWorkGraph, CredentialEnvelope, LocalDevVerifier
from devgraph.auth.scopes import SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE
from devgraph.client import DevgraphHttpClient
from devgraph.events.outbox import EventOutbox
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.relationships import RelationshipGraph
from devgraph.remote import RemoteGateway
from devgraph.remote.responses import TransportSafeProjector
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph-remote-contract"
FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
EXPIRED = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class RemoteCredentials:
    full: str
    wrong_scope: str
    unknown: str
    expired: str
    wrong_audience: str


@dataclass(frozen=True)
class RemoteAuthorityFixture:
    gateway: RemoteGateway
    credentials: RemoteCredentials
    observation: ObservationHandles


def _envelope(
    name: str,
    *,
    scopes: frozenset[str],
    expires_at: datetime = FUTURE,
    audience: str = AUDIENCE,
) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id=f"synthetic-actor-{name}",
        session_id=f"synthetic-session-{name}",
        correlation_id=f"synthetic-correlation-{name}",
        scopes=scopes,
        expires_at=expires_at,
        issuer="synthetic-remote-contract-issuer",
        audience=audience,
    )


def build_remote_authority_fixture(*, verifier_available: bool = True) -> RemoteAuthorityFixture:
    storage = MemoryGraphStorage()
    repository = RepositoryDelegateSpy(WorkObjectRepository(storage))
    audit_log = AuditLog()
    verifier = LocalDevVerifier(auth_mode="local-dev" if verifier_available else None)
    credentials = RemoteCredentials(
        full="opaque-remote-full",
        wrong_scope="opaque-remote-wrong-scope",
        unknown="opaque-remote-unknown",
        expired="opaque-remote-expired",
        wrong_audience="opaque-remote-wrong-audience",
    )
    verifier.register(
        credentials.full,
        _envelope("full", scopes=frozenset({SCOPE_READ, SCOPE_WRITE})),
    )
    verifier.register(
        credentials.wrong_scope,
        _envelope("wrong-scope", scopes=frozenset({SCOPE_ADMIN})),
    )
    verifier.register(
        credentials.expired,
        _envelope("expired", scopes=frozenset({SCOPE_READ, SCOPE_WRITE}), expires_at=EXPIRED),
    )
    verifier.register(
        credentials.wrong_audience,
        _envelope(
            "wrong-audience",
            scopes=frozenset({SCOPE_READ, SCOPE_WRITE}),
            audience="other-audience",
        ),
    )
    outbox = EventOutbox(storage, receipt_id_factory=_receipt_id_factory())
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
        repository=repository,
    )
    services = ApiServices(
        authorized_graph=graph,
        outbox=outbox,
        storage=storage,
        verifier=verifier,
        audience=AUDIENCE,
    )
    transport = TestClient(create_app(services), raise_server_exceptions=False)
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://testserver",
        timeout=1.0,
    )
    return RemoteAuthorityFixture(
        gateway=RemoteGateway(
            client=client,
            projector=TransportSafeProjector(),
            request_id_factory=lambda: "rmt_abcdefghijklmnopqrstuvwxyz",
        ),
        credentials=credentials,
        observation=ObservationHandles(storage, repository, audit_log, outbox),
    )


def _receipt_id_factory():
    next_id = 0

    def make_id() -> str:
        nonlocal next_id
        next_id += 1
        return f"event-receipt-remote-contract-{next_id}"

    return make_id
