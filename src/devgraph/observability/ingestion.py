"""Session metrics ingestion: protocol plus manual import adapter.

The Hermes metrics source is not available in this environment
(GitHub #12 pinned decision 6), so v0 ships this seam with a manual
import adapter validated against synthetic fixtures. There is no
dependency on Hermes internals. Payloads pass through the Issue 8
redaction seam before any field extraction, and only keys in the
pinned field contract survive — everything else is dropped fail-closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from devgraph.auth.context import AuthorityContext
from devgraph.observability.hermes_session_ref import HermesSessionRef
from devgraph.policy.redaction import redact_mapping

_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "external_session_id",
        "profile_source",
        "model_provider",
        "token_count_prompt",
        "token_count_completion",
        "token_count_total",
        "tool_call_count",
        "started_at",
        "ended_at",
    }
)


@runtime_checkable
class SessionMetricsIngestion(Protocol):
    """Ingestion seam: external session payload -> validated reference."""

    def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        authority: AuthorityContext,
        ref_id: str,
    ) -> HermesSessionRef: ...


class ManualImportAdapter:
    """Parses operator-supplied synthetic/exported payload mappings."""

    def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        authority: AuthorityContext,
        ref_id: str,
    ) -> HermesSessionRef:
        redacted = redact_mapping(payload)
        cleaned = {
            key: value
            for key, value in redacted.items()
            if key in _ALLOWED_PAYLOAD_KEYS
        }
        external_session_id = str(cleaned.get("external_session_id", "")).strip()
        if not external_session_id:
            raise ValueError("payload is missing external_session_id")
        return HermesSessionRef(
            id=ref_id,
            external_session_id=external_session_id,
            actor_id=authority.actor_id,
            session_id=authority.session_id,
            correlation_id=authority.correlation_id,
            profile_source=str(cleaned.get("profile_source", "")),
            model_provider=str(cleaned.get("model_provider", "")),
            token_count_prompt=_optional_int(cleaned.get("token_count_prompt")),
            token_count_completion=_optional_int(
                cleaned.get("token_count_completion")
            ),
            token_count_total=_optional_int(cleaned.get("token_count_total")),
            tool_call_count=_optional_int(cleaned.get("tool_call_count")),
            started_at=_optional_datetime(cleaned.get("started_at")),
            ended_at=_optional_datetime(cleaned.get("ended_at")),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
