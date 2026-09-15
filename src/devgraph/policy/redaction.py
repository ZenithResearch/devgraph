"""Redaction helper for strings, mappings, and event-like payloads.

Secrets are not canonical DB fields, but sensitive payloads can cross
export/log/error/event boundaries anyway (decision table D30). This
helper is the single scrubbing point those boundaries share.

Two complementary rules:

- **Key rule** — a mapping key that names credential material loses its
  entire value, recursively. Safe correlation identifiers
  (``actor_id``/``session_id``/``correlation_id``) are deliberately not
  sensitive: audit records and safe errors depend on them. ``token_count``
  style aggregates are also preserved here — omitting them from unsafe
  exports is the export filter's job, not string scrubbing.
- **Value rule** — string values are scanned for credential-shaped
  content (bearer tokens, ``secret=…`` assignments) and scrubbed in
  place.

The placeholder is idempotent: redacting already-redacted output is a
no-op.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED_PLACEHOLDER = "[REDACTED]"

# A key is sensitive when a whole underscore/dash-separated segment names
# credential material, or when it is/ends with "token". "token_count"
# stays: it does not end with "_token" and its segments are aggregates,
# not credential values.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(?:credential|secret|password|passphrase|macaroon|"
    r"authorization|cookie|api[_-]?key|bearer|private[_-]?key)s?(?:$|[_-])"
    r"|(?:^|[_-])token$",
    re.IGNORECASE,
)

_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:secret|token|credential|password|passphrase|macaroon|"
        r"api[_-]?key)s?\s*[:=]\s*\S+"
    ),
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


def redact_text(value: str) -> str:
    """Scrub credential-shaped content from a string, preserving the
    surrounding safe text."""
    result = value
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        result = pattern.sub(REDACTED_PLACEHOLDER, result)
    return result


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_redact_value(item) for item in value]
    return value


def redact_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of ``payload``; the input is never mutated."""
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_sensitive_key(key):
            redacted[key] = REDACTED_PLACEHOLDER
        else:
            redacted[key] = _redact_value(value)
    return redacted


def redact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Redact an event-like payload (event receipts, audit exports).

    Same contract as :func:`redact_mapping`; named separately so event
    boundaries (Issue #17) depend on a stable entry point."""
    return redact_mapping(event)
