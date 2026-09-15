"""Commit 6: adversarial redaction proofs across observability surfaces.

Acceptance evidence for GitHub #12: synthetic transcript-, prompt-, and
credential-shaped markers must never survive into a persisted
HermesSessionRef node, an emitted log record, or trace metadata — via
any supported path. Everything below is synthetic.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.model.base import utc_now
from devgraph.observability.attachment import HermesSessionAttachmentService
from devgraph.observability.hermes_session_ref import HERMES_SESSION_REF_LABEL
from devgraph.observability.ingestion import ManualImportAdapter
from devgraph.observability.logging import correlated_logger
from devgraph.observability.tracing import NoOpTracer
from devgraph.storage.memory import MemoryGraphStorage

FAKE_SECRET = "FAKE-SECRET-obs-31"
FAKE_TOKEN = "FAKE-TOKEN-obs-32"
FAKE_TRANSCRIPT = "SYNTHETIC-TRANSCRIPT-BODY-must-not-persist"


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


def _hostile_payload() -> dict:
    return {
        "external_session_id": "hermes-session-synthetic-009",
        "profile_source": f"frank token: {FAKE_TOKEN}",
        "model_provider": f"provider secret={FAKE_SECRET}",
        "transcript": FAKE_TRANSCRIPT,
        "raw_prompt": FAKE_TRANSCRIPT,
        "credential": FAKE_SECRET,
        "token_count_total": 10,
    }


class TestPersistedNodeNeverLeaks:
    def test_hostile_payload_through_ingest_and_attach(self) -> None:
        storage = MemoryGraphStorage()
        storage.create_node("Issue", "issue-1", {"title": "safe"})
        ref = ManualImportAdapter().ingest(
            _hostile_payload(), authority=_authority(), ref_id="hsr-adv"
        )
        HermesSessionAttachmentService(storage).attach(
            ref, subject_label="Issue", subject_id="issue-1"
        )
        node = storage.get_node(HERMES_SESSION_REF_LABEL, "hsr-adv")
        rendered = json.dumps(node.properties)
        for marker in (FAKE_SECRET, FAKE_TOKEN, FAKE_TRANSCRIPT):
            assert marker not in rendered, marker


class TestLogsNeverLeak:
    def test_hostile_message_and_extras(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="devgraph.test.redaction")
        log = correlated_logger(
            "devgraph.test.redaction",
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
        )
        log.info(
            f"ingested with Authorization: Bearer {FAKE_TOKEN}",
            extra={"macaroon": FAKE_SECRET, "note": f"secret={FAKE_SECRET}"},
        )
        record = caplog.records[-1]
        rendered = record.getMessage() + str(record.macaroon) + str(record.note)
        assert FAKE_TOKEN not in rendered
        assert FAKE_SECRET not in rendered
        assert record.correlation_id == "corr-0001"


class TestTraceMetadataNeverLeaks:
    def test_span_context_carries_only_name_and_correlation(self) -> None:
        with NoOpTracer().span("ingest", correlation_id="corr-0001") as span:
            fields = set(vars(span))
            assert fields == {"name", "correlation_id"}
