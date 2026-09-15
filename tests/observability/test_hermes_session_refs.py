"""Commit 1: HermesSessionRef model and storage serialization.

Field contract pinned by GitHub #12 and decision doc 0013: references
and redundant/queryable metrics only — Hermes stays canonical.
Unsupplied optional metrics persist as empty/None, never fabricated.
All fixtures are synthetic.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

from devgraph.observability.hermes_session_ref import (
    HERMES_SESSION_REF_LABEL,
    HermesSessionRef,
)

PINNED_FIELD_NAMES = {
    "id",
    "external_session_id",
    "actor_id",
    "session_id",
    "correlation_id",
    "profile_source",
    "model_provider",
    "token_count_prompt",
    "token_count_completion",
    "token_count_total",
    "tool_call_count",
    "started_at",
    "ended_at",
    "created_at",
    "updated_at",
}


def _full_ref() -> HermesSessionRef:
    started = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)
    return HermesSessionRef(
        id="hsr-1",
        external_session_id="hermes-session-synthetic-001",
        profile_source="frank/matrix",
        model_provider="synthetic-provider/synthetic-model",
        token_count_prompt=120,
        token_count_completion=80,
        token_count_total=200,
        tool_call_count=4,
        started_at=started,
        ended_at=ended,
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
    )


class TestFieldContract:
    def test_label_is_pinned(self) -> None:
        assert HERMES_SESSION_REF_LABEL == "HermesSessionRef"

    def test_field_set_matches_pinned_contract_exactly(self) -> None:
        # The exact-set assertion is the forbidden-content guard: no field
        # can carry transcripts, prompts, payloads, or credential material
        # because no field outside the pinned contract can exist at all.
        assert {f.name for f in fields(HermesSessionRef)} == PINNED_FIELD_NAMES

    def test_not_a_work_object_subclass(self) -> None:
        from devgraph.model.base import WorkObject

        assert not issubclass(HermesSessionRef, WorkObject)

    def test_unsupplied_optional_metrics_stay_empty(self) -> None:
        ref = HermesSessionRef(
            id="hsr-2",
            external_session_id="hermes-session-synthetic-002",
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
        )
        assert ref.profile_source == ""
        assert ref.model_provider == ""
        assert ref.token_count_prompt is None
        assert ref.token_count_completion is None
        assert ref.token_count_total is None
        assert ref.tool_call_count is None
        assert ref.started_at is None
        assert ref.ended_at is None


class TestSerialization:
    def test_round_trip_preserves_all_fields(self) -> None:
        ref = _full_ref()
        restored = HermesSessionRef.from_node_properties(
            ref.id, ref.to_node_properties()
        )
        assert restored == ref

    def test_round_trip_with_unsupplied_metrics(self) -> None:
        ref = HermesSessionRef(
            id="hsr-3",
            external_session_id="hermes-session-synthetic-003",
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
        )
        restored = HermesSessionRef.from_node_properties(
            ref.id, ref.to_node_properties()
        )
        assert restored == ref

    def test_properties_are_storage_safe_scalars(self) -> None:
        props = _full_ref().to_node_properties()
        for key, value in props.items():
            assert isinstance(value, (str, int)) or value is None, (key, value)
