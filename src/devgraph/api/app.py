"""FastAPI app factory wiring existing devgraph services.

The factory receives already-constructed services — it never builds
storage, verifiers, or policy objects itself, so the API layer carries
no configuration or enforcement logic of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import Lifespan

from devgraph.auth.enforcement import AuthorizedWorkGraph
from devgraph.auth.secs_monitor_view_read import SecSMonitorViewReadAdapter
from devgraph.auth.verifier import CredentialVerifier
from devgraph.events.outbox import EventOutbox
from devgraph.ops.migrate import Manifest, readiness_status
from devgraph.ops.retained_audit import AuditUnavailable, RetainedAuditLog
from devgraph.storage.base import GraphStorage, MigrationStore
from devgraph.storage.cypher_read import CypherReadService


class NamedWorkReceiver(Protocol):
    def execute(self, *, request_json: bytes, projection_json: bytes, idempotency_key: str): ...


@dataclass(frozen=True)
class ApiServices:
    """Existing service seams the API adapts. Nothing here is API-owned.

    ``verifier``/``audience`` mirror what the façade already holds: the
    idempotent write executor verifies per call for receipt authority
    context, exactly like the façade does for enforcement."""

    authorized_graph: AuthorizedWorkGraph
    outbox: EventOutbox
    storage: GraphStorage
    verifier: CredentialVerifier
    audience: str
    migration_manifest: Manifest | None = None
    migration_store: MigrationStore | None = None
    monitor_view_read: SecSMonitorViewReadAdapter | None = None
    named_work: NamedWorkReceiver | None = None
    retained_audit: RetainedAuditLog | None = None
    cypher_read: CypherReadService | None = None


def create_app(services: ApiServices, *, lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    app = FastAPI(title="devgraph", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.services = services
    if services.retained_audit is not None:
        from devgraph.api.audit import RetainedAuditMiddleware

        app.add_middleware(RetainedAuditMiddleware, audit_log=services.retained_audit)

    @app.get("/live")
    def live() -> dict[str, bool]:
        return {"live": True}

    @app.get("/ready")
    def ready() -> JSONResponse:
        status = readiness_status(
            services.migration_manifest,
            services.migration_store,
            services.storage,
        )
        if services.retained_audit is not None:
            try:
                services.retained_audit.check()
            except AuditUnavailable:
                return JSONResponse(status_code=503, content={
                    **status.safe_output(), "ready": False, "reason": "audit_unavailable",
                })
        return JSONResponse(
            status_code=200 if status.ready else 503,
            content=status.safe_output(),
        )

    from devgraph.api.arenas import register_arenas
    from devgraph.api.cypher_read import register_cypher_read
    from devgraph.api.errors import register_error_handlers
    from devgraph.api.named_work import register_named_work
    from devgraph.api.routes import register_routes

    register_error_handlers(app)
    register_routes(app, services)
    from devgraph.frontend.selection import register_selection

    register_selection(app)
    register_named_work(app, services)
    register_arenas(app, services)
    register_cypher_read(app, services)
    return app
