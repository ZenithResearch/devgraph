"""HermesSessionRef references, structured logging, tracing seam, ingestion.

Owned by GitHub #12. Hermes remains canonical for Hermes sessions;
devgraph stores references and redundant/queryable metrics only —
never transcripts, prompts, tool payloads, Matrix messages, or
credential material.
"""

from devgraph.observability.hermes_session_ref import (
    HERMES_SESSION_REF_LABEL,
    HermesSessionRef,
)

__all__ = ["HERMES_SESSION_REF_LABEL", "HermesSessionRef"]
