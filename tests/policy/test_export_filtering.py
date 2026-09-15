"""Commit 3: export filtering by mode.

Internal exports carry full payloads; redacted exports omit private
records and scrub what remains; the public-safe summary reduces
everything to counts; denied is a structured safe envelope, never a
silent downgrade. All sensitive fixtures are synthetic markers.
"""

from __future__ import annotations

import json

import pytest

from devgraph.model.artifacts import Artifact, ArtifactRole
from devgraph.model.work import Issue, Proposal, Task
from devgraph.policy.export import (
    ExportMode,
    denied_export,
    export_records,
)

FAKE_MARKER = "FAKE-SECRET-marker-777"


def _records() -> list:
    return [
        Issue(id="is-1", title="public issue", description="safe text"),
        Task(id="ta-1", title="task", description=f"note secret={FAKE_MARKER} end"),
        Proposal(id="p-1", title="private strategy proposal"),
        Artifact(
            id="a-1",
            title="review packet",
            role=ArtifactRole.REVIEW_PACKET,
            summary=f"contains token: {FAKE_MARKER}",
        ),
    ]


class TestInternalExport:
    def test_internal_includes_private_records_and_full_payloads(self) -> None:
        result = export_records(_records(), ExportMode.INTERNAL)
        assert result["mode"] == "internal"
        ids = {record["id"] for record in result["records"]}
        assert ids == {"is-1", "ta-1", "p-1", "a-1"}
        # Internal is the trusted surface: payloads are not scrubbed.
        assert FAKE_MARKER in json.dumps(result)


class TestRedactedExport:
    def test_private_records_are_omitted_and_counted(self) -> None:
        result = export_records(_records(), ExportMode.REDACTED)
        assert result["mode"] == "redacted"
        ids = {record["id"] for record in result["records"]}
        assert ids == {"is-1", "ta-1"}
        assert result["omitted_private"] == 2

    def test_remaining_payloads_are_scrubbed(self) -> None:
        result = export_records(_records(), ExportMode.REDACTED)
        assert FAKE_MARKER not in json.dumps(result)

    def test_private_reference_ids_are_stripped_from_included_records(self) -> None:
        # Every Artifact/ExternalLink is partition-private in v0, so a
        # safe record's reference fields must come out empty in redacted
        # mode even when the referenced records are not in the batch.
        issue = Issue(
            id="is-ref",
            title="public issue with private references",
            artifact_ids=("a-outside-batch",),
            external_link_ids=("l-outside-batch",),
        )
        result = export_records([issue], ExportMode.REDACTED)
        (record,) = result["records"]
        assert record["artifact_ids"] == []
        assert record["external_link_ids"] == []
        rendered = json.dumps(result)
        assert "a-outside-batch" not in rendered
        assert "l-outside-batch" not in rendered


class TestPublicSafeSummary:
    def test_summary_is_counts_only(self) -> None:
        result = export_records(_records(), ExportMode.PUBLIC_SAFE_SUMMARY)
        assert result["mode"] == "public_safe_summary"
        assert result["total"] == 4
        assert result["by_kind"] == {
            "Issue": 1,
            "Task": 1,
            "Proposal": 1,
            "Artifact": 1,
        }
        assert result["private_records"] == 2

    def test_summary_carries_no_titles_ids_or_payloads(self) -> None:
        result = export_records(_records(), ExportMode.PUBLIC_SAFE_SUMMARY)
        rendered = json.dumps(result)
        assert FAKE_MARKER not in rendered
        for leaked in ("is-1", "p-1", "public issue", "private strategy"):
            assert leaked not in rendered


class TestDenied:
    def test_export_records_rejects_denied_mode(self) -> None:
        with pytest.raises(ValueError):
            export_records(_records(), ExportMode.DENIED)

    def test_denied_export_is_a_safe_envelope(self) -> None:
        result = denied_export(correlation_id="corr-9")
        assert result == {
            "mode": "denied",
            "records": [],
            "reason": "export scope not granted",
            "correlation_id": "corr-9",
        }

    def test_denied_export_without_correlation_id(self) -> None:
        result = denied_export()
        assert result["correlation_id"] is None
        assert result["records"] == []
