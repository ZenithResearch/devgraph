from __future__ import annotations

from devgraph.model.external_links import (
    ExternalLink,
    ExternalLinkRole,
    SyncShadow,
    parse_github_url,
)


def test_external_link_model_has_role_taxonomy_and_serializable_properties():
    link = ExternalLink(
        id="link-1",
        title="Implementation issue",
        role=ExternalLinkRole.GITHUB_ISSUE,
        url="https://github.com/ZenithResearch/devgraph/issues/7",
        external_id="ZenithResearch/devgraph#7",
        summary="GitHub issue tracking the PR boundary",
    )

    assert link.kind == "ExternalLink"
    assert link.role == ExternalLinkRole.GITHUB_ISSUE
    assert link.url == "https://github.com/ZenithResearch/devgraph/issues/7"

    props = link.to_node_properties()
    assert props["role"] == "github_issue"
    assert props["url"] == "https://github.com/ZenithResearch/devgraph/issues/7"
    assert props["external_id"] == "ZenithResearch/devgraph#7"
    assert props["summary"] == "GitHub issue tracking the PR boundary"


def test_external_link_roles_cover_github_without_linear_support():
    assert {role.value for role in ExternalLinkRole} == {
        "github_issue",
        "github_pr",
        "github_repo",
        "github_commit",
        "generic_url",
    }
    assert "linear" not in {role.value for role in ExternalLinkRole}


def test_parse_github_url_classifies_supported_github_references():
    assert parse_github_url("https://github.com/ZenithResearch/devgraph") == (
        ExternalLinkRole.GITHUB_REPO,
        "ZenithResearch/devgraph",
    )
    assert parse_github_url("https://github.com/ZenithResearch/devgraph/issues/7") == (
        ExternalLinkRole.GITHUB_ISSUE,
        "ZenithResearch/devgraph#7",
    )
    assert parse_github_url("https://github.com/ZenithResearch/devgraph/pull/42") == (
        ExternalLinkRole.GITHUB_PR,
        "ZenithResearch/devgraph!42",
    )
    assert parse_github_url(
        "https://github.com/ZenithResearch/devgraph/commit/abcdef123456"
    ) == (ExternalLinkRole.GITHUB_COMMIT, "ZenithResearch/devgraph@abcdef123456")


def test_parse_github_url_rejects_non_github_and_unsupported_shapes():
    assert parse_github_url("https://linear.app/acme/issue/ABC-1") is None
    assert parse_github_url("https://github.com/ZenithResearch/devgraph/releases/tag/v1") is None


def test_sync_shadow_is_optional_non_authoritative_metadata():
    shadow = SyncShadow(
        provider="github",
        external_id="ZenithResearch/devgraph#7",
        external_updated_at="2026-06-29T00:00:00Z",
        cached_state="open",
        authoritative=False,
    )

    assert shadow.provider == "github"
    assert shadow.external_id == "ZenithResearch/devgraph#7"
    assert shadow.authoritative is False
    assert shadow.to_properties() == {
        "provider": "github",
        "external_id": "ZenithResearch/devgraph#7",
        "external_updated_at": "2026-06-29T00:00:00Z",
        "cached_state": "open",
        "authoritative": False,
    }


def test_external_link_status_transition_preserves_link_specific_fields():
    link = ExternalLink(
        id="link-1",
        title="Implementation issue",
        role=ExternalLinkRole.GITHUB_ISSUE,
        url="https://github.com/ZenithResearch/devgraph/issues/7",
        external_id="ZenithResearch/devgraph#7",
        summary="Issue boundary",
    )

    updated = link.with_status(link.status)

    assert updated.role == link.role
    assert updated.url == link.url
    assert updated.external_id == link.external_id
    assert updated.summary == link.summary
