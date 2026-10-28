"""Closed retry-identity lookup shared by canonical storage implementations."""

import re

from devgraph.storage.base import StorageUnavailable

RECEIPT_DIGEST_FIELDS = frozenset({"idempotency_claim_digest", "idempotency_key_digest"})


def validate_receipt_lookup(property_name: str, digest: str) -> None:
    if (
        not isinstance(property_name, str)
        or property_name not in RECEIPT_DIGEST_FIELDS
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise StorageUnavailable("invalid receipt lookup")
