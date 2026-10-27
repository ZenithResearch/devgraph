"""SessionRef reads; mutations use signed named Work operations."""

from fastapi import Header, HTTPException, Path, Query

from devgraph.api.routes import _credential


def register_session_refs(app, services):
    @app.get("/session-refs/v1")
    def page(
        authorization: str | None = Header(default=None),
        after: str | None = Query(default=None, pattern=r"^sr-[0-9a-f]{64}$"),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return services.authorized_graph.session_references(
            _credential(authorization), after=after, limit=limit
        )

    @app.get("/session-refs/v1/{ref_id}")
    def detail(
        ref_id: str = Path(pattern=r"^sr-[0-9a-f]{64}$"),
        authorization: str | None = Header(default=None),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        result = services.authorized_graph.session_references(
            _credential(authorization), ref_id=ref_id, limit=limit
        )
        if result is None:
            raise HTTPException(404, "session_reference_not_found")
        return result
