"""Explicit standing-grant validity, separate from finite access credentials.

This is a new wire contract. Existing drafts retain their original finite fields
and may use ``dated`` as a semantic adapter without changing signed bytes.
Revocation, policy status, scope and signer validity remain independent checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MAX_SAFE = 9007199254740991
SCHEMA = "castalia.grant-validity.v1"


def _seconds(value):
    if type(value) is not int or not 0 <= value <= MAX_SAFE:
        raise ValueError("invalid grant validity")
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate grant validity field")
        result[key] = value
    return result


@dataclass(frozen=True)
class GrantValidity:
    not_before: int
    not_after: int | None

    def __post_init__(self):
        _seconds(self.not_before)
        if self.not_after is not None and _seconds(self.not_after) <= self.not_before:
            raise ValueError("invalid grant interval")

    @classmethod
    def dated(cls, not_before, not_after):
        _seconds(not_after)
        return cls(not_before, not_after)

    @classmethod
    def until_revoked(cls, not_before):
        return cls(not_before, None)

    @classmethod
    def parse(cls, raw: bytes):
        if not isinstance(raw, bytes) or len(raw) > 1024:
            raise ValueError("invalid grant validity")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("invalid grant validity") from None
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ValueError("unsupported grant validity")
        if value.get("mode") == "dated" and set(value) == {
            "schema",
            "mode",
            "not_before",
            "not_after",
        }:
            return cls.dated(value["not_before"], value["not_after"])
        if value.get("mode") == "until_revoked" and set(value) == {"schema", "mode", "not_before"}:
            return cls.until_revoked(value["not_before"])
        raise ValueError("invalid grant validity")

    def to_json(self) -> bytes:
        value = {"schema": SCHEMA, "mode": "until_revoked", "not_before": self.not_before}
        if self.not_after is not None:
            value.update(mode="dated", not_after=self.not_after)
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def contains(self, now: int) -> bool:
        _seconds(now)
        return self.not_before <= now and (self.not_after is None or now < self.not_after)

    def contained_by(self, parent: GrantValidity) -> bool:
        return self.not_before >= parent.not_before and (
            parent.not_after is None
            or self.not_after is not None
            and self.not_after <= parent.not_after
        )

    def credential_deadline(self, now: int, lifetime: int) -> int:
        if not self.contains(now) or type(lifetime) is not int or not 1 <= lifetime <= 3600:
            raise ValueError("grant inactive or invalid credential lifetime")
        deadline = now + lifetime
        if self.not_after is not None:
            deadline = min(deadline, self.not_after)
        return _seconds(deadline)
