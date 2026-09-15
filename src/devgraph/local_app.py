"""Explicitly local, memory-backed app for the Dev Graph monitor.

This module is a development fixture, not a production server or credential
adapter.  It only accepts its registered token when ``DEVGRAPH_AUTH_MODE`` is
exactly ``local-dev``.
"""

from __future__ import annotations

import os
from datetime import timedelta

from devgraph.api import ApiServices, create_app
from devgraph.auth import AuditLog, AuthorizedWorkGraph, CredentialEnvelope, LocalDevVerifier
from devgraph.auth.scopes import SCOPE_READ
from devgraph.events.outbox import EventOutbox
from devgraph.model.base import utc_now
from devgraph.model.initiative_observations import (
    InitiativeObservation,
    InitiativeObservationRepository,
    InitiativeObservationSubjectKind,
)
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue, Project
from devgraph.relationships import RelationshipGraph
from devgraph.storage.base import GraphStorage
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph"
DEFAULT_LOCAL_TOKEN = "fake-credential-monitor"


def create_local_app():
    storage = MemoryGraphStorage()
    token = os.environ.get("DEVGRAPH_DEV_TOKEN", DEFAULT_LOCAL_TOKEN)
    verifier = LocalDevVerifier.from_env()
    verifier.register(
        token,
        CredentialEnvelope(
            actor_id="local-monitor-operator",
            session_id="local-monitor-session",
            correlation_id="local-monitor-correlation",
            scopes=frozenset({SCOPE_READ}),
            expires_at=utc_now() + timedelta(hours=12),
            issuer="devgraph-local-fixture",
            audience=AUDIENCE,
        ),
    )
    repository = WorkObjectRepository(storage)
    observations = InitiativeObservationRepository(storage)
    relationships = RelationshipGraph(storage)
    authorized_graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=relationships,
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=AuditLog(),
        repository=repository,
        initiative_observations=observations,
        monitor_storage=storage,
    )
    if os.environ.get("DEVGRAPH_MONITOR_DEMO") == "1":
        _seed_synthetic_demo(repository, observations, relationships, storage)
    return create_app(
        ApiServices(
            authorized_graph=authorized_graph,
            outbox=EventOutbox(storage),
            storage=storage,
            verifier=verifier,
            audience=AUDIENCE,
        )
    )


def _seed_synthetic_demo(
    repository: WorkObjectRepository,
    observations: InitiativeObservationRepository,
    relationships: RelationshipGraph,
    storage: GraphStorage,
) -> None:
    """Populate obvious synthetic records for local visual verification."""

    initiative = Initiative(
        id="demo-federated-discovery", title="Federated discovery layer"
    )
    project = Project(
        id="demo-initiative-contract", title="Initiative observation contract"
    )
    issue = Issue(id="demo-monitor-ui", title="Build graph operations monitor")
    for work in (initiative, project, issue):
        repository.create(work)
    relationships.add_parent(project, initiative)
    relationships.add_parent(issue, project)
    observation = observations.create(
        InitiativeObservation(
            id="demo-observation-ambient-context",
            project_id="external-ambient-context",
            subject_kind=InitiativeObservationSubjectKind.GITHUB_REPOSITORY,
            subject_url="https://github.com/dragthelake/ambient-context",
            source_commit="abcdef1",
            title="Local ambient evidence fabric",
            problem="Agents lose the context surrounding work between sessions.",
            desired_state="A local-first evidence timeline that agents can inspect safely.",
            evidence_urls=(
                "https://github.com/dragthelake/ambient-context",
            ),
            confidence=0.88,
            observed_by="devgraph-demo-scout",
        )
    )
    storage.create_edge(
        project.kind,
        project.id,
        "HAS_ARTIFACT",
        "Artifact",
        observation.id,
    )


app = create_local_app()
