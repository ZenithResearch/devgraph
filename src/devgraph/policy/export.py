"""Export modes and private-resource taxonomy.

Locked by the redaction fixture boundary decision: export policy must
distinguish exactly four outcomes — full internal export, redacted
export, denied export, and a public-safe metadata summary. The
private-resource taxonomy mirrors decision table entry D16: private
records are not assumed to be credentials stored in the DB; they are
ordinary planning/evidence records whose payloads must not cross an
unsafe export boundary.

Classification here is deliberately kind/role based and fail-closed:
proposals, artifacts, review packets, decisions, and external links are
private by default. Plain work items (Initiative/Project/Issue/Task and
supporting records) are not private records, but non-internal modes
still redact or summarize their fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any

from devgraph.model.artifacts import Artifact, ArtifactRole
from devgraph.model.base import WorkObject
from devgraph.model.external_links import ExternalLink
from devgraph.model.work import Decision, Proposal, ReviewPacket
from devgraph.policy.redaction import redact_mapping


class ExportMode(str, Enum):
    INTERNAL = "internal"
    REDACTED = "redacted"
    DENIED = "denied"
    PUBLIC_SAFE_SUMMARY = "public_safe_summary"


class PrivateResourceCategory(str, Enum):
    """D16 private-resource categories subject to omission/redaction."""

    PRIVATE_PROPOSAL = "private_proposal"
    REVIEW_EVIDENCE = "review_evidence"
    ARTIFACT_PAYLOAD = "artifact_payload"
    PRIVATE_EXTERNAL_LINK = "private_external_link"
    ACTOR_SESSION_METADATA = "actor_session_metadata"
    RAW_IMPORTED_PAYLOAD = "raw_imported_payload"
    AUTH_ENVELOPE_METADATA = "auth_envelope_metadata"


_REVIEW_EVIDENCE_ARTIFACT_ROLES = frozenset(
    {ArtifactRole.REVIEW_PACKET, ArtifactRole.EVIDENCE}
)


def classify_private_category(
    record: WorkObject,
) -> PrivateResourceCategory | None:
    """Return the D16 category for a private record, or ``None`` for
    plain work items that are not private records themselves.

    Payload-level categories (actor/session metadata, raw imported
    payloads, auth envelope metadata) are handled by the redaction
    helper; they never classify a whole node."""
    if isinstance(record, Proposal):
        return PrivateResourceCategory.PRIVATE_PROPOSAL
    if isinstance(record, Artifact):
        if record.role in _REVIEW_EVIDENCE_ARTIFACT_ROLES:
            return PrivateResourceCategory.REVIEW_EVIDENCE
        return PrivateResourceCategory.ARTIFACT_PAYLOAD
    if isinstance(record, (ReviewPacket, Decision)):
        return PrivateResourceCategory.REVIEW_EVIDENCE
    if isinstance(record, ExternalLink):
        return PrivateResourceCategory.PRIVATE_EXTERNAL_LINK
    return None


def is_private_for_export(record: WorkObject) -> bool:
    """True when the record is omitted from non-internal exports by default."""
    return classify_private_category(record) is not None


def _record_payload(record: WorkObject) -> dict[str, Any]:
    return {"id": record.id, **record.to_node_properties()}


# Reference fields every WorkObject payload carries that point at
# partition-private record kinds (all Artifacts and ExternalLinks are
# private in v0). A safe parent record leaking these IDs would reveal
# private object existence and graph linkage across an unsafe boundary,
# so redacted exports empty them wholesale — fail-closed even for
# referenced records outside the export batch.
_PRIVATE_REFERENCE_FIELDS = ("artifact_ids", "external_link_ids")


def _strip_private_reference_ids(payload: dict[str, Any]) -> dict[str, Any]:
    for field in _PRIVATE_REFERENCE_FIELDS:
        if field in payload:
            payload[field] = []
    return payload


def export_records(
    records: Iterable[WorkObject],
    mode: ExportMode,
) -> dict[str, Any]:
    """Filter ``records`` for one export mode.

    - internal: every record, full payload — the trusted surface.
    - redacted: private records omitted (and counted), remaining
      payloads passed through the redaction helper.
    - public_safe_summary: counts by kind plus a private-record count;
      no ids, titles, or payload fields of any record.

    ``DENIED`` is not a filtering mode — denial is enforced before
    filtering runs (see :func:`denied_export`); asking this function to
    filter for it is a caller bug."""
    materialized = list(records)
    if mode is ExportMode.INTERNAL:
        return {
            "mode": mode.value,
            "records": [_record_payload(record) for record in materialized],
        }
    if mode is ExportMode.REDACTED:
        included = [
            _strip_private_reference_ids(redact_mapping(_record_payload(record)))
            for record in materialized
            if not is_private_for_export(record)
        ]
        omitted = len(materialized) - len(included)
        return {
            "mode": mode.value,
            "records": included,
            "omitted_private": omitted,
        }
    if mode is ExportMode.PUBLIC_SAFE_SUMMARY:
        by_kind: dict[str, int] = {}
        for record in materialized:
            by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        return {
            "mode": mode.value,
            "total": len(materialized),
            "by_kind": by_kind,
            "private_records": sum(
                1 for record in materialized if is_private_for_export(record)
            ),
        }
    raise ValueError(f"{mode.value!r} is not a filtering mode")


def denied_export(*, correlation_id: str | None = None) -> dict[str, Any]:
    """Structured safe denial envelope for surfaces that need a body
    instead of an exception. Carries no records and no claim contents —
    only the safe correlation id for audit lookup."""
    return {
        "mode": ExportMode.DENIED.value,
        "records": [],
        "reason": "export scope not granted",
        "correlation_id": correlation_id,
    }
