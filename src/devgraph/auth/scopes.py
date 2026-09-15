"""Scope vocabulary and operation-category policy matrix for devgraph authorization.

Specified by Issue #7 and the "scoped credential envelope v0" decision
(see ``docs/auth/credential-envelope.md``). This module defines policy
vocabulary only; it performs no enforcement.

Policy rule: every grant is explicit. The admin scope does not implicitly
satisfy any other operation category — broad DB access is a granted scope,
not implied by agent identity. Export categories are policy vocabulary only;
export operations are owned by Issue #16.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

SCOPE_READ = "devgraph.read"
SCOPE_WRITE = "devgraph.write"
SCOPE_ADMIN = "devgraph.admin"
SCOPE_TOOL_USE = "devgraph.tool.use"
SCOPE_SKILL_USE = "devgraph.skill.use"
SCOPE_EXPORT_INTERNAL = "devgraph.export.internal"
SCOPE_EXPORT_REDACTED = "devgraph.export.redacted"

ALL_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_READ,
        SCOPE_WRITE,
        SCOPE_ADMIN,
        SCOPE_TOOL_USE,
        SCOPE_SKILL_USE,
        SCOPE_EXPORT_INTERNAL,
        SCOPE_EXPORT_REDACTED,
    }
)

CATEGORY_READ = "read"
CATEGORY_WRITE = "write"
CATEGORY_ADMIN = "admin"
CATEGORY_EXPORT_INTERNAL = "export.internal"
CATEGORY_EXPORT_REDACTED = "export.redacted"
CATEGORY_TOOL_USE = "tool.use"
CATEGORY_SKILL_USE = "skill.use"

ALL_CATEGORIES: frozenset[str] = frozenset(
    {
        CATEGORY_READ,
        CATEGORY_WRITE,
        CATEGORY_ADMIN,
        CATEGORY_EXPORT_INTERNAL,
        CATEGORY_EXPORT_REDACTED,
        CATEGORY_TOOL_USE,
        CATEGORY_SKILL_USE,
    }
)

# Each operation category requires exactly its own dedicated scope.
# Admin intentionally satisfies only the admin category.
REQUIRED_SCOPE_BY_CATEGORY: Mapping[str, str] = MappingProxyType(
    {
        CATEGORY_READ: SCOPE_READ,
        CATEGORY_WRITE: SCOPE_WRITE,
        CATEGORY_ADMIN: SCOPE_ADMIN,
        CATEGORY_EXPORT_INTERNAL: SCOPE_EXPORT_INTERNAL,
        CATEGORY_EXPORT_REDACTED: SCOPE_EXPORT_REDACTED,
        CATEGORY_TOOL_USE: SCOPE_TOOL_USE,
        CATEGORY_SKILL_USE: SCOPE_SKILL_USE,
    }
)
