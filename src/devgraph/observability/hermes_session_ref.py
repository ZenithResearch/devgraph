"""HermesSessionRef model: external session reference plus redundant metrics.

Field contract pinned by GitHub #12 and the HermesSessionRef ingestion
boundary decision (0013). This is a first-class storage label like
EventReceipt — not a WorkObject subclass and not user-facing work.
Optional metrics are ``None``/empty when the external source did not
supply them; devgraph never fabricates metric values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

HERMES_SESSION_REF_LABEL = "HermesSessionRef"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class HermesSessionRef:
    id: str
    external_session_id: str
    actor_id: str
    session_id: str
    correlation_id: str
    profile_source: str = ""
    model_provider: str = ""
    token_count_prompt: int | None = None
    token_count_completion: int | None = None
    token_count_total: int | None = None
    tool_call_count: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_node_properties(self) -> dict[str, Any]:
        return {
            "external_session_id": self.external_session_id,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "profile_source": self.profile_source,
            "model_provider": self.model_provider,
            "token_count_prompt": self.token_count_prompt,
            "token_count_completion": self.token_count_completion,
            "token_count_total": self.token_count_total,
            "tool_call_count": self.tool_call_count,
            "started_at": _datetime_to_storage(self.started_at),
            "ended_at": _datetime_to_storage(self.ended_at),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_node_properties(
        cls, ref_id: str, properties: Mapping[str, Any]
    ) -> HermesSessionRef:
        return cls(
            id=ref_id,
            external_session_id=str(properties["external_session_id"]),
            actor_id=str(properties["actor_id"]),
            session_id=str(properties["session_id"]),
            correlation_id=str(properties["correlation_id"]),
            profile_source=str(properties.get("profile_source", "")),
            model_provider=str(properties.get("model_provider", "")),
            token_count_prompt=_optional_int(properties.get("token_count_prompt")),
            token_count_completion=_optional_int(
                properties.get("token_count_completion")
            ),
            token_count_total=_optional_int(properties.get("token_count_total")),
            tool_call_count=_optional_int(properties.get("tool_call_count")),
            started_at=_datetime_from_storage(properties.get("started_at")),
            ended_at=_datetime_from_storage(properties.get("ended_at")),
            created_at=_required_datetime(properties["created_at"]),
            updated_at=_required_datetime(properties["updated_at"]),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _datetime_to_storage(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _datetime_from_storage(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _required_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
