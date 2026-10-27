"""Source-owned session refs, legacy Hermes compatibility, and capture seams.

Owned by GitHub #12. Hermes remains canonical for Hermes sessions;
devgraph stores references and redundant/queryable metrics only —
never transcripts, prompts, tool payloads, Matrix messages, or
credential material.
"""

from devgraph.observability.hermes_session_ref import (
    HERMES_SESSION_REF_LABEL,
    HermesSessionRef,
)
from devgraph.observability.session_ref import SessionIdentity, SessionLink, SessionRef

__all__ = [
    "HERMES_SESSION_REF_LABEL", "HermesSessionRef", "SessionIdentity", "SessionLink", "SessionRef",
]
