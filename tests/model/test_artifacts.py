from __future__ import annotations

from devgraph.model.artifacts import Artifact, ArtifactRole


def test_artifact_model_has_role_taxonomy_and_serializable_properties():
    artifact = Artifact(
        id="artifact-1",
        title="Review packet PDF",
        role=ArtifactRole.REVIEW_PACKET,
        uri="file://evidence/review-packet.pdf",
        media_type="application/pdf",
        summary="Human-reviewed packet summary",
    )

    assert artifact.kind == "Artifact"
    assert artifact.role == ArtifactRole.REVIEW_PACKET
    assert artifact.uri == "file://evidence/review-packet.pdf"

    props = artifact.to_node_properties()
    assert props["role"] == "review_packet"
    assert props["uri"] == "file://evidence/review-packet.pdf"
    assert props["media_type"] == "application/pdf"
    assert props["summary"] == "Human-reviewed packet summary"


def test_artifact_roles_cover_v0_evidence_and_export_surfaces():
    assert {role.value for role in ArtifactRole} == {
        "proposal_document",
        "review_packet",
        "evidence",
        "export",
        "screenshot",
        "log_summary",
        "initiative_observation",
    }


def test_artifact_status_transition_preserves_artifact_specific_fields():
    artifact = Artifact(
        id="artifact-1",
        title="Evidence screenshot",
        role=ArtifactRole.SCREENSHOT,
        uri="file://evidence/screenshot.png",
        media_type="image/png",
        summary="UI evidence",
    )

    updated = artifact.with_status(artifact.status)

    assert updated.role == artifact.role
    assert updated.uri == artifact.uri
    assert updated.media_type == artifact.media_type
    assert updated.summary == artifact.summary
