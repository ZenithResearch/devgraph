"""Transport-neutral InitiativeObservation v0 artifact profile.

An observation records an external scout's evidence-backed reading of a public
project.  It is deliberately not an ``Initiative`` work object and does not
claim maintainer authorship, repository control, or federation membership.

Persistence uses the existing ``Artifact`` label and uniqueness constraint.
That keeps this change additive: older readers continue to treat the node as a
generic artifact while aware readers can decode the typed profile from
``role=initiative_observation`` and ``schema_version``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from devgraph.model.base import utc_now
from devgraph.model.validation import validate_page_limit, validate_work_object_id
from devgraph.storage.base import GraphStorage, NodeRecord

INITIATIVE_OBSERVATION_SCHEMA_VERSION = "devgraph.initiative-observation.v0"
INITIATIVE_OBSERVATION_LABEL = "Artifact"
INITIATIVE_OBSERVATION_ROLE = "initiative_observation"

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$", re.ASCII)


class InitiativeObservationSubjectKind(str, Enum):
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ORGANIZATION = "github_organization"


class InitiativeObservationClaimStatus(str, Enum):
    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"
    AMENDED = "amended"
    REJECTED = "rejected"


def normalize_github_subject_url(
    subject_kind: InitiativeObservationSubjectKind,
    value: str,
) -> str:
    """Return the canonical GitHub web locator for the requested subject kind."""

    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_github_subject_url")
    parts = [part for part in parsed.path.split("/") if part]
    expected_parts = (
        2
        if subject_kind is InitiativeObservationSubjectKind.GITHUB_REPOSITORY
        else 1
    )
    if len(parts) != expected_parts:
        raise ValueError("invalid_github_subject_url")
    if expected_parts == 2 and parts[1].endswith(".git"):
        parts[1] = parts[1][:-4]
    if any(not part for part in parts):
        raise ValueError("invalid_github_subject_url")
    return f"https://github.com/{'/'.join(part.lower() for part in parts)}"


def _validate_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("invalid_evidence_url")
    return value


@dataclass(frozen=True)
class InitiativeObservation:
    id: str
    project_id: str
    subject_kind: InitiativeObservationSubjectKind
    subject_url: str
    title: str
    problem: str
    desired_state: str
    evidence_urls: tuple[str, ...]
    observed_by: str
    confidence: float
    github_node_id: str = ""
    source_commit: str = ""
    claim_status: InitiativeObservationClaimStatus = (
        InitiativeObservationClaimStatus.UNCLAIMED
    )
    observation_signature: str = ""
    observed_at: datetime = field(default_factory=utc_now)
    schema_version: str = INITIATIVE_OBSERVATION_SCHEMA_VERSION
    authorship: str = "inferred"

    def __post_init__(self) -> None:
        validate_work_object_id(self.id)
        validate_work_object_id(self.project_id)
        if self.schema_version != INITIATIVE_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported_initiative_observation_schema")
        if self.authorship != "inferred":
            raise ValueError("initiative_observation_authorship_must_be_inferred")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.title, self.problem, self.desired_state, self.observed_by)
        ):
            raise ValueError("initiative_observation_required_text_missing")
        normalized = normalize_github_subject_url(self.subject_kind, self.subject_url)
        object.__setattr__(self, "subject_url", normalized)
        if not isinstance(self.evidence_urls, tuple) or not self.evidence_urls:
            raise ValueError("initiative_observation_evidence_required")
        object.__setattr__(
            self,
            "evidence_urls",
            tuple(_validate_https_url(value) for value in self.evidence_urls),
        )
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (float, int))
            or not math.isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise ValueError("initiative_observation_confidence_out_of_range")
        object.__setattr__(self, "confidence", float(self.confidence))
        if self.source_commit and _COMMIT_PATTERN.fullmatch(self.source_commit) is None:
            raise ValueError("invalid_source_commit")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at_must_be_timezone_aware")

    def to_node_properties(self) -> dict[str, Any]:
        return {
            "kind": type(self).__name__,
            "role": INITIATIVE_OBSERVATION_ROLE,
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "subject_kind": self.subject_kind.value,
            "subject_url": self.subject_url,
            "github_node_id": self.github_node_id,
            "source_commit": self.source_commit,
            "title": self.title,
            "problem": self.problem,
            "desired_state": self.desired_state,
            "evidence_urls": list(self.evidence_urls),
            "confidence": self.confidence,
            "authorship": self.authorship,
            "claim_status": self.claim_status.value,
            "observed_by": self.observed_by,
            "observation_signature": self.observation_signature,
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_node(cls, node: NodeRecord) -> InitiativeObservation:
        if (
            node.label != INITIATIVE_OBSERVATION_LABEL
            or node.properties.get("role") != INITIATIVE_OBSERVATION_ROLE
        ):
            raise InvalidInitiativeObservationError(
                f"invalid initiative observation artifact: {node.id}"
            )
        try:
            return cls(
                id=node.id,
                project_id=str(node.properties["project_id"]),
                subject_kind=InitiativeObservationSubjectKind(
                    str(node.properties["subject_kind"])
                ),
                subject_url=str(node.properties["subject_url"]),
                github_node_id=str(node.properties.get("github_node_id", "")),
                source_commit=str(node.properties.get("source_commit", "")),
                title=str(node.properties["title"]),
                problem=str(node.properties["problem"]),
                desired_state=str(node.properties["desired_state"]),
                evidence_urls=tuple(node.properties["evidence_urls"]),
                confidence=float(node.properties["confidence"]),
                authorship=str(node.properties["authorship"]),
                claim_status=InitiativeObservationClaimStatus(
                    str(node.properties["claim_status"])
                ),
                observed_by=str(node.properties["observed_by"]),
                observation_signature=str(
                    node.properties.get("observation_signature", "")
                ),
                observed_at=datetime.fromisoformat(str(node.properties["observed_at"])),
                schema_version=str(node.properties["schema_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInitiativeObservationError(
                f"invalid initiative observation artifact: {node.id}"
            ) from exc


class InitiativeObservationRepositoryError(ValueError):
    """Safe base error carrying identifiers but no observation prose."""


class MissingInitiativeObservationError(InitiativeObservationRepositoryError):
    pass


class InitiativeObservationAlreadyExistsError(InitiativeObservationRepositoryError):
    pass


class InvalidInitiativeObservationError(InitiativeObservationRepositoryError):
    pass


class InitiativeObservationRepository:
    """Append-only typed view over ``Artifact`` persistence."""

    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    def create(self, observation: InitiativeObservation) -> InitiativeObservation:
        if observation.claim_status is not InitiativeObservationClaimStatus.UNCLAIMED:
            raise InvalidInitiativeObservationError(
                "initiative observation must be unclaimed at creation"
            )
        with self._storage.transaction():
            if self._storage.get_node(INITIATIVE_OBSERVATION_LABEL, observation.id):
                raise InitiativeObservationAlreadyExistsError(
                    f"existing initiative observation: {observation.id}"
                )
            self._storage.create_node(
                INITIATIVE_OBSERVATION_LABEL,
                observation.id,
                observation.to_node_properties(),
            )
        return observation

    def get_by_id(self, observation_id: str) -> InitiativeObservation:
        validate_work_object_id(observation_id)
        node = self._storage.get_node(INITIATIVE_OBSERVATION_LABEL, observation_id)
        if node is None or node.properties.get("role") != INITIATIVE_OBSERVATION_ROLE:
            raise MissingInitiativeObservationError(
                f"missing initiative observation: {observation_id}"
            )
        return InitiativeObservation.from_node(node)

    def query(
        self,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int = 50,
    ) -> list[InitiativeObservation]:
        if after_id is not None:
            validate_work_object_id(after_id)
        validate_page_limit(limit)
        nodes = self._storage.query(
            INITIATIVE_OBSERVATION_LABEL,
            archived=False,
            descending=descending,
            after_id=after_id,
            limit=None,
        )
        observations = [
            InitiativeObservation.from_node(node)
            for node in nodes
            if node.properties.get("role") == INITIATIVE_OBSERVATION_ROLE
        ]
        return observations[:limit]
