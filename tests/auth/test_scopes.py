from __future__ import annotations

import pytest

from devgraph.auth import (
    ALL_CATEGORIES,
    ALL_SCOPES,
    CATEGORY_ADMIN,
    CATEGORY_EXPORT_INTERNAL,
    CATEGORY_EXPORT_REDACTED,
    CATEGORY_READ,
    CATEGORY_SKILL_USE,
    CATEGORY_TOOL_USE,
    CATEGORY_WRITE,
    REQUIRED_SCOPE_BY_CATEGORY,
    SCOPE_ADMIN,
    SCOPE_EXPORT_INTERNAL,
    SCOPE_EXPORT_REDACTED,
    SCOPE_READ,
    SCOPE_SKILL_USE,
    SCOPE_TOOL_USE,
    SCOPE_WRITE,
)


def test_scope_vocabulary_is_exactly_the_seven_issue_scopes():
    assert ALL_SCOPES == frozenset(
        {
            "devgraph.read",
            "devgraph.write",
            "devgraph.admin",
            "devgraph.tool.use",
            "devgraph.skill.use",
            "devgraph.export.internal",
            "devgraph.export.redacted",
        }
    )


def test_scope_constants_match_their_vocabulary_strings():
    assert SCOPE_READ == "devgraph.read"
    assert SCOPE_WRITE == "devgraph.write"
    assert SCOPE_ADMIN == "devgraph.admin"
    assert SCOPE_TOOL_USE == "devgraph.tool.use"
    assert SCOPE_SKILL_USE == "devgraph.skill.use"
    assert SCOPE_EXPORT_INTERNAL == "devgraph.export.internal"
    assert SCOPE_EXPORT_REDACTED == "devgraph.export.redacted"


def test_policy_matrix_covers_every_category_with_exactly_one_known_scope():
    assert set(REQUIRED_SCOPE_BY_CATEGORY) == set(ALL_CATEGORIES)
    assert ALL_CATEGORIES == frozenset(
        {
            "read",
            "write",
            "admin",
            "export.internal",
            "export.redacted",
            "tool.use",
            "skill.use",
        }
    )
    for required_scope in REQUIRED_SCOPE_BY_CATEGORY.values():
        assert required_scope in ALL_SCOPES


def test_policy_matrix_maps_each_category_to_its_dedicated_scope():
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_READ] == SCOPE_READ
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_WRITE] == SCOPE_WRITE
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_ADMIN] == SCOPE_ADMIN
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_EXPORT_INTERNAL] == SCOPE_EXPORT_INTERNAL
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_EXPORT_REDACTED] == SCOPE_EXPORT_REDACTED
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_TOOL_USE] == SCOPE_TOOL_USE
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_SKILL_USE] == SCOPE_SKILL_USE
    # No two categories share a required scope: every grant is explicit.
    required_scopes = list(REQUIRED_SCOPE_BY_CATEGORY.values())
    assert len(set(required_scopes)) == len(required_scopes)


def test_admin_scope_does_not_satisfy_read_or_write_categories():
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_READ] != SCOPE_ADMIN
    assert REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_WRITE] != SCOPE_ADMIN


def test_admin_scope_satisfies_only_the_admin_category():
    categories_satisfied_by_admin = [
        category
        for category, required_scope in REQUIRED_SCOPE_BY_CATEGORY.items()
        if required_scope == SCOPE_ADMIN
    ]
    assert categories_satisfied_by_admin == [CATEGORY_ADMIN]


def test_policy_matrix_is_read_only():
    with pytest.raises(TypeError):
        REQUIRED_SCOPE_BY_CATEGORY[CATEGORY_READ] = SCOPE_ADMIN  # type: ignore[index]
