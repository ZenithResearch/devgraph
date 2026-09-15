"""Commit 4: structured logging with correlation IDs.

Stdlib logging only. Every emitted record carries the devgraph
authority identifiers, and both the message and extra fields pass
through the Issue 8 redaction seam. All markers are synthetic.
"""

from __future__ import annotations

import logging

import pytest

from devgraph.observability.logging import correlated_logger

FAKE_MARKER = "FAKE-SECRET-log-77"


@pytest.fixture
def capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="devgraph.test")
    return caplog


class TestCorrelatedLogger:
    def test_records_carry_correlation_identifiers(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test",
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
        )
        log.info("operation completed", extra={"operation": "attach"})

        record = capture.records[-1]
        assert record.actor_id == "agent-frank"
        assert record.session_id == "session-0001"
        assert record.correlation_id == "corr-0001"
        assert record.operation == "attach"

    def test_message_is_scrubbed(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test",
            actor_id="a",
            session_id="s",
            correlation_id="c",
        )
        log.info(f"call used token: {FAKE_MARKER}")
        assert FAKE_MARKER not in capture.records[-1].getMessage()

    def test_extra_fields_are_scrubbed(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test",
            actor_id="a",
            session_id="s",
            correlation_id="c",
        )
        log.info("ok", extra={"detail": f"secret={FAKE_MARKER}", "api_key": FAKE_MARKER})
        record = capture.records[-1]
        assert FAKE_MARKER not in str(record.detail)
        assert record.api_key == "[REDACTED]"

    def test_positional_args_are_redacted(self, capture) -> None:
        # Stdlib interpolates args after LoggerAdapter.process, so raw
        # args would bypass the redaction seam; the adapter must render
        # and scrub them itself (PR #23 review blocker).
        log = correlated_logger(
            "devgraph.test", actor_id="a", session_id="s", correlation_id="c"
        )
        log.info("unsafe arg token: %s", FAKE_MARKER)
        assert FAKE_MARKER not in capture.records[-1].getMessage()
        assert "[REDACTED]" in capture.records[-1].getMessage()

    def test_mapping_args_are_redacted(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test", actor_id="a", session_id="s", correlation_id="c"
        )
        log.info("value token: %(leak)s end", {"leak": FAKE_MARKER})
        assert FAKE_MARKER not in capture.records[-1].getMessage()

    def test_normal_arg_formatting_is_preserved(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test", actor_id="a", session_id="s", correlation_id="c"
        )
        log.info("processed %d items for %s", 5, "issue-1")
        assert capture.records[-1].getMessage() == "processed 5 items for issue-1"

    def test_mismatched_format_never_drops_args_unredacted(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test", actor_id="a", session_id="s", correlation_id="c"
        )
        log.info("no placeholders", f"secret={FAKE_MARKER}")
        message = capture.records[-1].getMessage()
        assert FAKE_MARKER not in message

    def test_caller_extra_cannot_spoof_authority_fields(self, capture) -> None:
        log = correlated_logger(
            "devgraph.test",
            actor_id="real-actor",
            session_id="real-session",
            correlation_id="real-corr",
        )
        log.info(
            "ok",
            extra={
                "actor_id": "spoofed-actor",
                "session_id": "spoofed-session",
                "correlation_id": "spoofed-corr",
            },
        )
        record = capture.records[-1]
        assert record.actor_id == "real-actor"
        assert record.session_id == "real-session"
        assert record.correlation_id == "real-corr"

    def test_uses_stdlib_logging_only(self) -> None:
        import devgraph.observability.logging as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "structlog" not in source
        assert "loguru" not in source
