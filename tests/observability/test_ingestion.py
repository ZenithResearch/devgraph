"""Commit 3: metrics ingestion interface and manual import adapter.

The Hermes session metrics source is unavailable in this environment,
so ingestion is an interface plus a manual import adapter over
synthetic fixture payloads only. Payloads pass through the Issue 8
redaction seam before field extraction; keys outside the pinned
contract are dropped fail-closed. All fixtures are synthetic.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.model.base import utc_now
from devgraph.observability.ingestion import (
    ManualImportAdapter,
    SessionMetricsIngestion,
)

FAKE_MARKER = "FAKE-SECRET-ingest-55"


def _authority() -> AuthorityContext:
    return AuthorityContext(
        envelope=CredentialEnvelope(
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=utc_now() + timedelta(hours=1),
            issuer="devgraph-test-issuer",
            audience="devgraph",
        )
    )


def _payload() -> dict:
    return {
        "external_session_id": "hermes-session-synthetic-001",
        "profile_source": "frank/matrix",
        "model_provider": "synthetic-provider/synthetic-model",
        "token_count_prompt": 120,
        "token_count_completion": 80,
        "token_count_total": 200,
        "tool_call_count": 4,
        "started_at": "2026-07-06T12:00:00+00:00",
        "ended_at": "2026-07-06T12:30:00+00:00",
    }


class TestManualImportAdapter:
    def test_adapter_satisfies_ingestion_protocol(self) -> None:
        assert isinstance(ManualImportAdapter(), SessionMetricsIngestion)

    def test_valid_synthetic_payload_ingests(self) -> None:
        ref = ManualImportAdapter().ingest(
            _payload(), authority=_authority(), ref_id="hsr-1"
        )
        assert ref.id == "hsr-1"
        assert ref.external_session_id == "hermes-session-synthetic-001"
        assert ref.token_count_total == 200
        assert ref.tool_call_count == 4
        assert ref.actor_id == "agent-frank"
        assert ref.correlation_id == "corr-0001"
        assert ref.started_at is not None and ref.started_at.hour == 12

    def test_partial_payload_leaves_metrics_unsupplied(self) -> None:
        ref = ManualImportAdapter().ingest(
            {"external_session_id": "hermes-session-synthetic-002"},
            authority=_authority(),
            ref_id="hsr-2",
        )
        assert ref.token_count_total is None
        assert ref.tool_call_count is None
        assert ref.profile_source == ""

    def test_missing_external_session_id_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            ManualImportAdapter().ingest(
                {"token_count_total": 5}, authority=_authority(), ref_id="hsr-3"
            )

    def test_unknown_keys_are_dropped(self) -> None:
        payload = _payload()
        payload["transcript"] = "synthetic transcript body that must not survive"
        payload["surprise_field"] = "unexpected"
        ref = ManualImportAdapter().ingest(
            payload, authority=_authority(), ref_id="hsr-4"
        )
        rendered = json.dumps(ref.to_node_properties())
        assert "synthetic transcript body" not in rendered
        assert "surprise_field" not in rendered

    def test_credential_shaped_content_is_scrubbed_before_extraction(self) -> None:
        payload = _payload()
        payload["profile_source"] = f"frank/matrix token: {FAKE_MARKER}"
        ref = ManualImportAdapter().ingest(
            payload, authority=_authority(), ref_id="hsr-5"
        )
        assert FAKE_MARKER not in json.dumps(ref.to_node_properties())

    def test_no_hermes_imports_in_module(self) -> None:
        import devgraph.observability.ingestion as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "import hermes" not in source
        assert "from hermes" not in source
