"""Display filtering for public HTTP problem details; no server/runtime imports."""

import re

_CREDENTIAL_TEXT = re.compile(
    r"Bearer\s+[A-Za-z0-9._~+/=-]+"
    r"|\b(?:secret|token|credential|password|passphrase|macaroon|api[_-]?key)s?\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def display_detail(detail: str) -> str:
    return _CREDENTIAL_TEXT.sub("[REDACTED]", detail)
