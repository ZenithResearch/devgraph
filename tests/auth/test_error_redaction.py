"""Commit 4: safe-error envelope redaction hooks.

Every AuthError message passes through the redaction helper at
construction, so even a future caller that interpolates a payload or
credential-shaped string into an error message cannot leak it. All
markers below are synthetic.
"""

from __future__ import annotations

import pytest

from devgraph.auth.errors import AuthError, ForbiddenError, UnauthenticatedError
from devgraph.policy.redaction import REDACTED_PLACEHOLDER


@pytest.mark.parametrize(
    "make_error",
    [
        lambda message: AuthError(message, category="unauthenticated"),
        lambda message: UnauthenticatedError(message),
        lambda message: ForbiddenError(message),
    ],
)
class TestErrorMessageRedaction:
    def test_credential_shaped_content_is_scrubbed(self, make_error) -> None:
        error = make_error("verify failed for token: FAKE-TOKEN-err-1")
        rendered = str(error)
        assert "FAKE-TOKEN-err-1" not in rendered
        assert REDACTED_PLACEHOLDER in rendered

    def test_bearer_header_echo_is_scrubbed(self, make_error) -> None:
        error = make_error("rejected header Authorization: Bearer FAKE-TOKEN-err-2")
        assert "FAKE-TOKEN-err-2" not in str(error)

    def test_safe_messages_are_unchanged(self, make_error) -> None:
        error = make_error("credential verification unavailable in this mode")
        assert str(error) == "credential verification unavailable in this mode"


class TestSafeFieldsPreserved:
    def test_category_and_correlation_id_survive_redaction(self) -> None:
        error = ForbiddenError(
            "scope for category 'write' not granted",
            correlation_id="corr-42",
        )
        assert error.category == "forbidden"
        assert error.correlation_id == "corr-42"
