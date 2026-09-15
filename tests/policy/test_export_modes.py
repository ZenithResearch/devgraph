"""Commit 1: export-mode vocabulary and private-resource taxonomy.

Locked by the redaction fixture boundary decision
(``docs/export-redaction.md`` mirrors it): exactly four export modes —
internal, redacted, denied, public-safe metadata summary — and a
private-resource taxonomy matching decision table entry D16. All
fixtures here are synthetic; no real secrets or private data appear.
"""

from __future__ import annotations

import pytest

from devgraph.model.artifacts import Artifact, ArtifactRole
from devgraph.model.external_links import ExternalLink, ExternalLinkRole
from devgraph.model.work import (
    Blocker,
    Decision,
    Handoff,
    Initiative,
    Issue,
    Milestone,
    Project,
    Proposal,
    Requirement,
    ReviewPacket,
    Task,
    Todo,
)
from devgraph.policy.export import (
    ExportMode,
    PrivateResourceCategory,
    classify_private_category,
    is_private_for_export,
)


class TestExportModeVocabulary:
    def test_exactly_four_modes(self) -> None:
        assert {mode.value for mode in ExportMode} == {
            "internal",
            "redacted",
            "denied",
            "public_safe_summary",
        }

    def test_mode_values_are_stable_strings(self) -> None:
        assert ExportMode.INTERNAL.value == "internal"
        assert ExportMode.REDACTED.value == "redacted"
        assert ExportMode.DENIED.value == "denied"
        assert ExportMode.PUBLIC_SAFE_SUMMARY.value == "public_safe_summary"


class TestPrivateResourceTaxonomy:
    def test_categories_match_decision_d16(self) -> None:
        assert {category.value for category in PrivateResourceCategory} == {
            "private_proposal",
            "review_evidence",
            "artifact_payload",
            "private_external_link",
            "actor_session_metadata",
            "raw_imported_payload",
            "auth_envelope_metadata",
        }


class TestClassification:
    def test_proposal_is_private(self) -> None:
        proposal = Proposal(id="p-1", title="fake strategy proposal")
        assert (
            classify_private_category(proposal)
            is PrivateResourceCategory.PRIVATE_PROPOSAL
        )
        assert is_private_for_export(proposal)

    @pytest.mark.parametrize(
        "role",
        [ArtifactRole.REVIEW_PACKET, ArtifactRole.EVIDENCE],
    )
    def test_review_and_evidence_artifacts_are_review_evidence(
        self, role: ArtifactRole
    ) -> None:
        artifact = Artifact(id="a-1", title="fake packet", role=role)
        assert (
            classify_private_category(artifact)
            is PrivateResourceCategory.REVIEW_EVIDENCE
        )

    @pytest.mark.parametrize(
        "role",
        [
            ArtifactRole.PROPOSAL_DOCUMENT,
            ArtifactRole.EXPORT,
            ArtifactRole.SCREENSHOT,
            ArtifactRole.LOG_SUMMARY,
        ],
    )
    def test_other_artifact_roles_are_artifact_payload(
        self, role: ArtifactRole
    ) -> None:
        artifact = Artifact(id="a-2", title="fake payload", role=role)
        assert (
            classify_private_category(artifact)
            is PrivateResourceCategory.ARTIFACT_PAYLOAD
        )

    def test_review_packet_and_decision_are_review_evidence(self) -> None:
        packet = ReviewPacket(id="rp-1", title="fake review packet")
        decision = Decision(id="d-1", title="fake acceptance decision")
        assert (
            classify_private_category(packet)
            is PrivateResourceCategory.REVIEW_EVIDENCE
        )
        assert (
            classify_private_category(decision)
            is PrivateResourceCategory.REVIEW_EVIDENCE
        )

    def test_external_link_is_private(self) -> None:
        link = ExternalLink(
            id="l-1",
            title="fake link",
            url="https://github.com/example/example",
            role=ExternalLinkRole.GITHUB_REPO,
        )
        assert (
            classify_private_category(link)
            is PrivateResourceCategory.PRIVATE_EXTERNAL_LINK
        )
        assert is_private_for_export(link)

    @pytest.mark.parametrize(
        "record",
        [
            Todo(id="t-0", title="plain todo"),
            Initiative(id="i-1", title="initiative"),
            Project(id="pr-1", title="project"),
            Issue(id="is-1", title="issue"),
            Task(id="ta-1", title="task"),
            Requirement(id="r-1", title="requirement"),
            Blocker(id="b-1", title="blocker"),
            Handoff(id="h-1", title="handoff"),
            Milestone(id="m-1", title="milestone"),
        ],
    )
    def test_plain_work_items_are_not_private_records(self, record: Todo) -> None:
        assert classify_private_category(record) is None
        assert not is_private_for_export(record)
