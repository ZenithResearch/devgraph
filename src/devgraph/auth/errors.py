"""Typed safe errors raised by credential verification.

Per the service contract "Safe errors" rule, every message carried by
these exceptions must be safe: never a credential string, never an
envelope claim dump, never a driver trace. Callers may attach a safe
correlation id for audit lookup; nothing else identifying travels with
the error.

Defense in depth: the base class scrubs every message through the
redaction helper at construction, so credential-shaped content
interpolated into a message by any future caller cannot leak.
"""

from __future__ import annotations

from devgraph.policy.redaction import redact_text


class AuthError(Exception):
    """Base class for safe authentication/authorization errors."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(redact_text(message))
        self.category = category
        self.correlation_id = correlation_id


class UnauthenticatedError(AuthError):
    """401-style denial: missing, unknown, or expired credential;
    audience mismatch; or verification unavailable in this mode."""

    CATEGORY = "unauthenticated"

    def __init__(self, message: str, *, correlation_id: str | None = None) -> None:
        super().__init__(
            message,
            category=self.CATEGORY,
            correlation_id=correlation_id,
        )


class ForbiddenError(AuthError):
    """403-style denial: verified caller lacks the scope its operation
    category requires. The safe message names the denied operation
    category only (e.g. "scope for category 'write' not granted") —
    never credential values, never the envelope's scope contents."""

    CATEGORY = "forbidden"

    def __init__(self, message: str, *, correlation_id: str | None = None) -> None:
        super().__init__(
            message,
            category=self.CATEGORY,
            correlation_id=correlation_id,
        )
