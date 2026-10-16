"""Credential envelope model for devgraph authorization.

The envelope is a parsed claim set only. It never stores the opaque
credential string, so its repr/str cannot leak a raw token value.
Parsing and verification of opaque credentials are out of scope here
(later Issue #7 commits own the verifier seam).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from devgraph.auth.delegated_contract import ALL_DELEGATED_SCOPES
from devgraph.auth.scopes import ALL_SCOPES

_AUTHORITY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")


@dataclass(frozen=True)
class CredentialEnvelope:
    """Parsed claims carried by a scoped service credential."""

    actor_id: str
    session_id: str
    correlation_id: str
    scopes: frozenset[str]
    expires_at: datetime
    issuer: str
    audience: str
    redaction_partitions: tuple[str, ...] = ()
    credential_id: str | None = None
    credential_version: int | None = None
    read_grants: tuple[Any, ...] = ()
    lifecycle_status: str | None = None
    resource: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "actor_id",
            "session_id",
            "correlation_id",
            "issuer",
            "audience",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or _AUTHORITY_IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"invalid {field_name}")
        unknown_scopes = sorted(set(self.scopes) - ALL_SCOPES - ALL_DELEGATED_SCOPES)
        if unknown_scopes:
            raise ValueError(f"unknown scopes: {', '.join(unknown_scopes)}")
        if self.credential_id is not None and (
            not isinstance(self.credential_id, str)
            or _AUTHORITY_IDENTIFIER.fullmatch(self.credential_id) is None
        ):
            raise ValueError("invalid credential_id")
        if self.credential_version is not None and (
            not isinstance(self.credential_version, int)
            or isinstance(self.credential_version, bool)
            or self.credential_version < 1
        ):
            raise ValueError("invalid credential_version")

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at
