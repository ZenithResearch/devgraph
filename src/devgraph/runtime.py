"""Production composition root for the private Devgraph HTTP service.

Production accepts only fail-closed modes or the machine-local ``devgraph.read``
credential. That bearer cannot authorize writes. Signed named Work uses a
separate receiver with owner-held trust and closed operation/resource bindings.
The local development fixture verifier is never accepted by this runtime.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI

from devgraph.api import ApiServices, create_app
from devgraph.auth import (
    AUTH_MODE_LOCAL_READ,
    AuditLog,
    AuthorizedWorkGraph,
    LocalDevVerifier,
    LocalReadCredentialConfigurationError,
    LocalReadCredentialVerifier,
)
from devgraph.events.outbox import EventOutbox
from devgraph.model.initiative_observations import InitiativeObservationRepository
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.repository import WorkObjectRepository
from devgraph.ops.local_path_integrity import OWNERSHIP_DISABLED_DIAGNOSTIC
from devgraph.ops.migrate import load_manifest
from devgraph.ops.named_work_receiver import LocalNamedWorkReceiver
from devgraph.ops.retained_audit import RetainedAuditLog
from devgraph.ops.secs_monitor_view_read_receiver import (
    LocalSecSMonitorViewReadError,
    load_local_secs_monitor_view_read_adapter,
)
from devgraph.relationships import RelationshipGraph
from devgraph.storage.cypher_read import CypherReadService
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

MANIFEST_PATH = Path(str(files("devgraph").joinpath("resources/migrations/manifest.json")))

ENVIRONMENT_VARIABLE = "DEVGRAPH_ENVIRONMENT"
AUDIENCE_VARIABLE = "DEVGRAPH_AUDIENCE"
AUTH_MODE_VARIABLE = "DEVGRAPH_AUTH_MODE"
DATA_ROOT_VARIABLE = "DEVGRAPH_DATA_ROOT"
PRODUCTION_ENVIRONMENT = "production"
FAIL_CLOSED_AUTH_MODE = "fail-closed"
LEGACY_DISABLED_AUTH_MODE = "disabled"
SUPPORTED_PRODUCTION_AUTH_MODES = frozenset(
    {AUTH_MODE_LOCAL_READ, FAIL_CLOSED_AUTH_MODE, LEGACY_DISABLED_AUTH_MODE}
)


class RuntimeConfigurationError(RuntimeError):
    """Safe startup failure for an invalid production configuration."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeConfigurationError(f"missing required runtime setting: {name}")
    return value


def build_production_services() -> ApiServices:
    """Construct the canonical Neo4j-backed service graph.

    No connectivity check or migration is performed here.  Migrations are an
    explicit release operation and ``/ready`` independently proves their state.
    """

    environment = _required_environment(ENVIRONMENT_VARIABLE)
    if environment != PRODUCTION_ENVIRONMENT:
        raise RuntimeConfigurationError("production runtime requires production environment")

    auth_mode = _required_environment(AUTH_MODE_VARIABLE)
    if auth_mode not in SUPPORTED_PRODUCTION_AUTH_MODES:
        raise RuntimeConfigurationError("unsupported production auth mode")

    audience = _required_environment(AUDIENCE_VARIABLE)
    storage = Neo4jGraphStorage(Neo4jConfig.from_env())
    data_root_value = os.environ.get(DATA_ROOT_VARIABLE)
    data_root = None
    if data_root_value:
        data_root = Path(data_root_value)
        if not data_root.is_absolute():
            raise RuntimeConfigurationError("configured data root must be absolute")
    if auth_mode == AUTH_MODE_LOCAL_READ:
        if data_root is None:
            raise RuntimeConfigurationError("local read auth requires a configured data root")
        try:
            verifier = LocalReadCredentialVerifier(data_root=data_root, audience=audience)
        except LocalReadCredentialConfigurationError:
            raise RuntimeConfigurationError(
                "local read credential configuration is invalid"
            ) from None
    else:
        verifier = LocalDevVerifier(auth_mode=auth_mode)
    audit_log = RetainedAuditLog(data_root) if data_root is not None else AuditLog()
    repository = WorkObjectRepository(storage)
    initiative_observations = InitiativeObservationRepository(storage)
    authorized_graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=audience,
        audit_log=audit_log,
        repository=repository,
        initiative_observations=initiative_observations,
        monitor_storage=storage,
    )
    monitor_view_read = None
    if data_root is not None:
        try:
            monitor_view_read = load_local_secs_monitor_view_read_adapter(
                data_root=data_root,
                storage=storage,
                audit_log=audit_log,
            )
        except LocalSecSMonitorViewReadError as error:
            if str(error) == OWNERSHIP_DISABLED_DIAGNOSTIC:
                raise RuntimeConfigurationError(OWNERSHIP_DISABLED_DIAGNOSTIC) from None
            raise RuntimeConfigurationError("monitor receiver configuration is invalid") from None
    return ApiServices(
        authorized_graph=authorized_graph,
        outbox=EventOutbox(storage),
        storage=storage,
        verifier=verifier,
        audience=audience,
        migration_manifest=load_manifest(MANIFEST_PATH),
        migration_store=Neo4jMigrationStore(storage),
        monitor_view_read=monitor_view_read,
        named_work=(
            LocalNamedWorkReceiver(data_root=data_root, storage=storage, audit_log=audit_log)
            if data_root is not None
            else None
        ),
        retained_audit=audit_log if isinstance(audit_log, RetainedAuditLog) else None,
        cypher_read=(
            CypherReadService(
                storage.create_cypher_read_runner(), verifier=verifier,
                audience=audience, audit_log=audit_log,
            )
            if callable(getattr(storage, "create_cypher_read_runner", None))
            else None
        ),
    )


def create_production_app() -> FastAPI:
    """Uvicorn ``--factory`` entry point with bounded resource cleanup."""

    services = build_production_services()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            try:
                services.storage.close()
            finally:
                if services.cypher_read is not None:
                    services.cypher_read.close()

    return create_app(services, lifespan=lifespan)
