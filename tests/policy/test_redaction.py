"""Commit 2: redaction helper for strings, mappings, and event-like payloads.

Every sensitive fixture below is synthetic (``FAKE-SECRET-…`` /
``FAKE-TOKEN-…`` markers); no real secret, token, or private payload
appears. The helper's contract: sensitive keys lose their whole value,
sensitive value shapes are scrubbed in place, and safe correlation
identifiers (actor/session/correlation ids) are preserved because audit
and safe errors depend on them.
"""

from __future__ import annotations

from devgraph.policy.redaction import (
    REDACTED_PLACEHOLDER,
    redact_event,
    redact_mapping,
    redact_text,
)


class TestRedactText:
    def test_bearer_token_is_scrubbed(self) -> None:
        text = "call failed with Authorization: Bearer FAKE-TOKEN-abc123 retrying"
        result = redact_text(text)
        assert "FAKE-TOKEN-abc123" not in result
        assert REDACTED_PLACEHOLDER in result
        assert result.startswith("call failed with")
        assert result.endswith("retrying")

    def test_secret_assignment_is_scrubbed(self) -> None:
        text = "loaded config secret=FAKE-SECRET-9 from env"
        result = redact_text(text)
        assert "FAKE-SECRET-9" not in result
        assert REDACTED_PLACEHOLDER in result

    def test_plain_text_is_unchanged(self) -> None:
        text = "proposal p-1 accepted by decision d-1"
        assert redact_text(text) == text

    def test_redaction_is_idempotent(self) -> None:
        text = "token: FAKE-TOKEN-xyz"
        once = redact_text(text)
        assert redact_text(once) == once


class TestRedactMapping:
    def test_sensitive_keys_lose_their_whole_value(self) -> None:
        payload = {
            "credential": "FAKE-SECRET-1",
            "api_key": "FAKE-SECRET-2",
            "Authorization": "Bearer FAKE-TOKEN-3",
            "access_token": "FAKE-TOKEN-4",
            "title": "safe title",
        }
        result = redact_mapping(payload)
        assert result["credential"] == REDACTED_PLACEHOLDER
        assert result["api_key"] == REDACTED_PLACEHOLDER
        assert result["Authorization"] == REDACTED_PLACEHOLDER
        assert result["access_token"] == REDACTED_PLACEHOLDER
        assert result["title"] == "safe title"

    def test_nested_mappings_and_sequences_are_recursed(self) -> None:
        payload = {
            "meta": {"macaroon": "FAKE-SECRET-5", "note": "ok"},
            "items": [
                {"password": "FAKE-SECRET-6"},
                "inline secret=FAKE-SECRET-7 here",
            ],
        }
        result = redact_mapping(payload)
        assert result["meta"]["macaroon"] == REDACTED_PLACEHOLDER
        assert result["meta"]["note"] == "ok"
        assert result["items"][0]["password"] == REDACTED_PLACEHOLDER
        assert "FAKE-SECRET-7" not in result["items"][1]

    def test_sensitive_values_in_safe_keys_are_scrubbed(self) -> None:
        payload = {"description": "attach used Bearer FAKE-TOKEN-8"}
        result = redact_mapping(payload)
        assert "FAKE-TOKEN-8" not in result["description"]

    def test_safe_identifier_fields_are_preserved(self) -> None:
        payload = {
            "actor_id": "agent-a",
            "session_id": "sess-1",
            "correlation_id": "corr-1",
            "token_count": 42,
        }
        result = redact_mapping(payload)
        assert result == payload

    def test_original_mapping_is_not_mutated(self) -> None:
        payload = {"credential": "FAKE-SECRET-10"}
        redact_mapping(payload)
        assert payload["credential"] == "FAKE-SECRET-10"

    def test_non_string_scalars_pass_through(self) -> None:
        payload = {"version": 3, "priority": 0, "flag": True, "none": None}
        assert redact_mapping(payload) == payload


class TestRedactEvent:
    def test_event_payload_keeps_correlation_but_loses_sensitive_fields(self) -> None:
        event = {
            "operation": "accept_proposal",
            "actor_id": "agent-a",
            "session_id": "sess-1",
            "correlation_id": "corr-1",
            "payload": {"credential": "FAKE-SECRET-11"},
            "summary": "done; auth used token: FAKE-TOKEN-12",
        }
        result = redact_event(event)
        assert result["actor_id"] == "agent-a"
        assert result["session_id"] == "sess-1"
        assert result["correlation_id"] == "corr-1"
        assert result["payload"]["credential"] == REDACTED_PLACEHOLDER
        assert "FAKE-TOKEN-12" not in result["summary"]
