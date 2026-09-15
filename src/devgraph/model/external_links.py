from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from devgraph.model.base import WorkObject


class ExternalLinkRole(str, Enum):
    GITHUB_ISSUE = "github_issue"
    GITHUB_PR = "github_pr"
    GITHUB_REPO = "github_repo"
    GITHUB_COMMIT = "github_commit"
    GENERIC_URL = "generic_url"


def parse_github_url(url: str) -> tuple[ExternalLinkRole, str] | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    repo_id = f"{owner}/{repo}"
    if len(parts) == 2:
        return ExternalLinkRole.GITHUB_REPO, repo_id
    if len(parts) == 4 and parts[2] == "issues" and parts[3].isdigit():
        return ExternalLinkRole.GITHUB_ISSUE, f"{repo_id}#{parts[3]}"
    if len(parts) == 4 and parts[2] == "pull" and parts[3].isdigit():
        return ExternalLinkRole.GITHUB_PR, f"{repo_id}!{parts[3]}"
    if len(parts) == 4 and parts[2] == "commit" and parts[3]:
        return ExternalLinkRole.GITHUB_COMMIT, f"{repo_id}@{parts[3]}"
    return None


@dataclass(frozen=True)
class SyncShadow:
    provider: str
    external_id: str
    external_updated_at: str = ""
    cached_state: str = ""
    authoritative: bool = False

    def to_properties(self) -> dict[str, str | bool]:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "external_updated_at": self.external_updated_at,
            "cached_state": self.cached_state,
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True)
class ExternalLink(WorkObject):
    role: ExternalLinkRole = ExternalLinkRole.GENERIC_URL
    url: str = ""
    external_id: str = ""
    summary: str = ""

    def to_node_properties(self) -> dict[str, Any]:
        props = super().to_node_properties()
        props.update(
            {
                "role": self.role.value,
                "url": self.url,
                "external_id": self.external_id,
                "summary": self.summary,
            }
        )
        return props
