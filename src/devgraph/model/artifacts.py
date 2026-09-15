from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from devgraph.model.base import WorkObject


class ArtifactRole(str, Enum):
    PROPOSAL_DOCUMENT = "proposal_document"
    REVIEW_PACKET = "review_packet"
    EVIDENCE = "evidence"
    EXPORT = "export"
    SCREENSHOT = "screenshot"
    LOG_SUMMARY = "log_summary"
    INITIATIVE_OBSERVATION = "initiative_observation"


@dataclass(frozen=True)
class Artifact(WorkObject):
    role: ArtifactRole = ArtifactRole.EVIDENCE
    uri: str = ""
    media_type: str = ""
    summary: str = ""

    def to_node_properties(self) -> dict[str, Any]:
        props = super().to_node_properties()
        props.update(
            {
                "role": self.role.value,
                "uri": self.uri,
                "media_type": self.media_type,
                "summary": self.summary,
            }
        )
        return props
