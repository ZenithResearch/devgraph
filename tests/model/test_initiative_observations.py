from __future__ import annotations

from datetime import datetime

import pytest

from devgraph.model.artifacts import Artifact, ArtifactRole
from devgraph.model.initiative_observations import (
    INITIATIVE_OBSERVATION_SCHEMA_VERSION,
    InitiativeObservation,
    InitiativeObservationClaimStatus,
    InitiativeObservationRepository,
    InitiativeObservationSubjectKind,
    InvalidInitiativeObservationError,
)
from devgraph.storage.memory import MemoryGraphStorage


def observation(**overrides) -> InitiativeObservation:
    values = {
        "id": "observation-1",
        "project_id": "project-external-1",
        "subject_kind": InitiativeObservationSubjectKind.GITHUB_REPOSITORY,
        "subject_url": "https://github.com/Owner/Repository.git",
        "github_node_id": "R_kgDOExample",
        "source_commit": "abcdef123456",
        "title": "Portable initiative graph",
        "problem": "Public project intent is difficult to discover across hosts.",
        "desired_state": "Evidence-backed initiatives can be claimed and federated.",
        "evidence_urls": (
            "https://github.com/Owner/Repository/tree/abcdef123456/src",
        ),
        "confidence": 0.82,
        "observed_by": "scout-key-1",
    }
    values.update(overrides)
    return InitiativeObservation(**values)


def test_observation_is_inferred_artifact_profile_with_normalized_subject() -> None:
    item = observation()

    assert item.schema_version == INITIATIVE_OBSERVATION_SCHEMA_VERSION
    assert item.authorship == "inferred"
    assert item.subject_url == "https://github.com/owner/repository"
    assert item.claim_status.value == "unclaimed"
    assert item.to_node_properties()["role"] == "initiative_observation"


def test_organization_subject_is_supported_without_a_source_commit() -> None:
    item = observation(
        subject_kind=InitiativeObservationSubjectKind.GITHUB_ORGANIZATION,
        subject_url="https://github.com/ZenithResearch/",
        source_commit="",
    )

    assert item.subject_url == "https://github.com/zenithresearch"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"evidence_urls": ()}, "initiative_observation_evidence_required"),
        ({"confidence": 1.1}, "initiative_observation_confidence_out_of_range"),
        ({"authorship": "maintainer"}, "initiative_observation_authorship_must_be_inferred"),
        ({"source_commit": "not-a-sha"}, "invalid_source_commit"),
        ({"subject_url": "https://example.com/project"}, "invalid_github_subject_url"),
    ],
)
def test_observation_rejects_unearned_or_malformed_claims(changes, error) -> None:
    with pytest.raises(ValueError, match=error):
        observation(**changes)


def test_repository_round_trip_filters_other_artifacts() -> None:
    storage = MemoryGraphStorage()
    repository = InitiativeObservationRepository(storage)
    storage.create_node(
        "Artifact",
        "artifact-1",
        Artifact(
            id="artifact-1",
            title="Other evidence",
            role=ArtifactRole.EVIDENCE,
        ).to_node_properties(),
    )
    created = repository.create(observation())

    loaded = repository.get_by_id(created.id)
    assert loaded == created
    assert repository.query(limit=10) == [created]
    assert isinstance(loaded.observed_at, datetime)


def test_repository_fixes_new_observations_as_unclaimed() -> None:
    repository = InitiativeObservationRepository(MemoryGraphStorage())

    with pytest.raises(
        InvalidInitiativeObservationError,
        match="must be unclaimed at creation",
    ):
        repository.create(
            observation(claim_status=InitiativeObservationClaimStatus.CLAIMED)
        )


def test_artifact_taxonomy_includes_initiative_observation_profile() -> None:
    assert ArtifactRole.INITIATIVE_OBSERVATION.value == "initiative_observation"
