"""Commit 5: private-record omission proof through the authorized façade.

Acceptance evidence for Issue #16: a private proposal, an evidence
artifact, a review packet, and a private external link — each seeded
with a synthetic sensitive marker — must never appear in redacted or
public-safe-summary output, and the marker must never appear in any
non-internal surface (exports, denials, error messages). Everything
below is synthetic; no real secret or private payload exists here.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from devgraph.auth import (
    SCOPE_EXPORT_INTERNAL,
    SCOPE_EXPORT_REDACTED,
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    ForbiddenError,
    LocalDevVerifier,
)
from devgraph.model.artifacts import Artifact, ArtifactRole
from devgraph.model.base import utc_now
from devgraph.model.external_links import ExternalLink, ExternalLinkRole
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import Issue, Proposal, ReviewPacket
from devgraph.policy.export import denied_export
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph"
FAKE_MARKER = "FAKE-SECRET-omission-99"
INTERNAL_CREDENTIAL = "fake-credential-internal"
REDACTED_CREDENTIAL = "fake-credential-redacted"

PRIVATE_FRAGMENTS = (
    FAKE_MARKER,
    "p-private",
    "private strategy proposal",
    "a-evidence",
    "acceptance evidence payload",
    "rp-packet",
    "review packet for p-private",
    "l-private",
    "github.com/example/private-repo",
)


def _fixture_records() -> list:
    return [
        Issue(
            id="is-public",
            title="public issue",
            description="safe text",
            # The public record references the private records: their IDs
            # must not survive into non-internal output either.
            artifact_ids=("a-evidence",),
            external_link_ids=("l-private",),
        ),
        Proposal(
            id="p-private",
            title="private strategy proposal",
            description=f"contains secret={FAKE_MARKER}",
        ),
        Artifact(
            id="a-evidence",
            title="acceptance evidence payload",
            role=ArtifactRole.EVIDENCE,
            summary=f"log excerpt with token: {FAKE_MARKER}",
        ),
        ReviewPacket(
            id="rp-packet",
            title="review packet for p-private",
            description="reviewer notes not for client export",
        ),
        ExternalLink(
            id="l-private",
            title="private repo link",
            url="https://github.com/example/private-repo",
            role=ExternalLinkRole.GITHUB_REPO,
        ),
    ]


def _envelope(scopes: frozenset[str]) -> CredentialEnvelope:
    return CredentialEnvelope(
        actor_id="agent-frank",
        session_id="session-0001",
        correlation_id="corr-0001",
        scopes=scopes,
        expires_at=utc_now() + timedelta(hours=1),
        issuer="devgraph-test-issuer",
        audience=AUDIENCE,
    )


@pytest.fixture
def graph() -> AuthorizedWorkGraph:
    storage = MemoryGraphStorage()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(
        INTERNAL_CREDENTIAL, _envelope(frozenset({SCOPE_EXPORT_INTERNAL}))
    )
    verifier.register(
        REDACTED_CREDENTIAL, _envelope(frozenset({SCOPE_EXPORT_REDACTED}))
    )
    return AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=AuditLog(),
    )


class TestPrivateRecordOmission:
    def test_redacted_export_omits_every_private_record(self, graph) -> None:
        result = graph.export_redacted(REDACTED_CREDENTIAL, _fixture_records())
        rendered = json.dumps(result)
        for fragment in PRIVATE_FRAGMENTS:
            assert fragment not in rendered, fragment
        assert result["omitted_private"] == 4
        assert {record["id"] for record in result["records"]} == {"is-public"}

    def test_public_safe_summary_omits_every_private_fragment(self, graph) -> None:
        result = graph.export_public_safe_summary(
            REDACTED_CREDENTIAL, _fixture_records()
        )
        rendered = json.dumps(result)
        for fragment in PRIVATE_FRAGMENTS:
            assert fragment not in rendered, fragment
        assert "is-public" not in rendered

    def test_redacted_export_strips_private_reference_ids_from_safe_records(
        self, graph
    ) -> None:
        # PR #21 review finding: a safe parent record must not reveal
        # private object existence/linkage through its reference fields.
        result = graph.export_redacted(REDACTED_CREDENTIAL, _fixture_records())
        (record,) = result["records"]
        assert record["artifact_ids"] == []
        assert record["external_link_ids"] == []

    def test_internal_export_keeps_private_records_for_trusted_surface(
        self, graph
    ) -> None:
        result = graph.export_internal(INTERNAL_CREDENTIAL, _fixture_records())
        ids = {record["id"] for record in result["records"]}
        assert {"p-private", "a-evidence", "rp-packet", "l-private"} <= ids
        (public_record,) = [
            record for record in result["records"] if record["id"] == "is-public"
        ]
        assert public_record["artifact_ids"] == ["a-evidence"]
        assert public_record["external_link_ids"] == ["l-private"]


class TestMarkerNeverLeaksThroughDenialSurfaces:
    def test_denied_export_error_carries_no_record_content(self, graph) -> None:
        with pytest.raises(ForbiddenError) as excinfo:
            graph.export_internal(REDACTED_CREDENTIAL, _fixture_records())
        rendered = str(excinfo.value)
        for fragment in (*PRIVATE_FRAGMENTS, REDACTED_CREDENTIAL):
            assert fragment not in rendered, fragment

    def test_denied_envelope_carries_no_record_content(self) -> None:
        rendered = json.dumps(denied_export(correlation_id="corr-0001"))
        for fragment in PRIVATE_FRAGMENTS:
            assert fragment not in rendered, fragment
