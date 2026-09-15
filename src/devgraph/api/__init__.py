"""Thin HTTP API adapter over the devgraph service layers (GitHub #11).

FastAPI per decision doc 0018; thin-route constraints per decision doc
0012: routes parse/validate envelopes, call existing services, and
serialize responses — no Neo4j driver imports, no local lifecycle,
auth, redaction, or outbox logic.
"""

from devgraph.api.app import ApiServices, create_app

__all__ = ["ApiServices", "create_app"]
