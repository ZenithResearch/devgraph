"""Correlation-carrying structured logging over Python stdlib logging.

Every record emitted through :func:`correlated_logger` carries the
devgraph authority identifiers (`actor_id`/`session_id`/
`correlation_id`) as structured fields, and both the rendered message
and all extra fields pass through the Issue 8 redaction seam. No
logging framework dependency is used or authorized.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from devgraph.policy.redaction import redact_mapping, redact_text


def _render_message(msg: Any, args: tuple[Any, ...]) -> str:
    """Interpolate positional args the way ``LogRecord.getMessage`` would.

    Stdlib logging interpolates ``args`` only after
    ``LoggerAdapter.process`` runs, so raw args would bypass the
    redaction seam entirely. Rendering here (and passing no args down)
    guarantees the final message string is what gets scrubbed."""
    text = str(msg)
    if not args:
        return text
    try:
        if len(args) == 1 and isinstance(args[0], Mapping) and args[0]:
            return text % args[0]
        return text % args
    except (TypeError, ValueError, KeyError):
        # Mirror stdlib's tolerance of format mismatches without ever
        # dropping args on the floor unredacted.
        return f"{text} % {args!r}"


class _RedactingCorrelationAdapter(logging.LoggerAdapter):
    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(level):
            rendered = _render_message(msg, args)
            super().log(level, rendered, **kwargs)

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra: dict[str, Any] = {}
        supplied = kwargs.get("extra")
        if isinstance(supplied, Mapping):
            extra.update(redact_mapping(supplied))
        # Authority identifiers overlay last: caller extras must not be
        # able to spoof actor/session/correlation on the record.
        extra.update(self.extra or {})
        kwargs["extra"] = extra
        return redact_text(str(msg)), kwargs


def correlated_logger(
    name: str,
    *,
    actor_id: str,
    session_id: str,
    correlation_id: str,
) -> logging.LoggerAdapter:
    """Return a logger adapter stamping authority identifiers onto every
    record and scrubbing message/extra content through the redaction seam."""
    return _RedactingCorrelationAdapter(
        logging.getLogger(name),
        {
            "actor_id": actor_id,
            "session_id": session_id,
            "correlation_id": correlation_id,
        },
    )
