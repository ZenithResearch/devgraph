"""Devgraph-owned request bytes and consent disclosure for generic Wallet v2.

Wallet does not parse these application semantics. secS and this receiver derive
both from the same closed request; browser-provided summaries are never trusted.
The legacy Work/Arena canonical bytes and digests are deliberately unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from devgraph.auth.secs_issue_create import _canonical_json, _digest_idempotency_key
from devgraph.named_requests import parse_named_request

REQUEST_DOMAIN = b"devgraph.credential-request.v2\x00"


@dataclass(frozen=True)
class CredentialRequestBinding:
    request_bytes: bytes
    request_digest_sha256: str
    disclosure: dict
    disclosure_digest_sha256: str


def _display_safe(value: str) -> bool:
    return not any(
        ord(c) < 32
        or 127 <= ord(c) <= 159
        or ord(c) in {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
        for c in value
    )


def credential_request_binding(request_json: bytes, idempotency_key: str):
    request = parse_named_request(request_json)
    key_digest = _digest_idempotency_key(idempotency_key)
    wrapper = {
        "schema": "devgraph.credential-request.v2",
        "request": json.loads(request.canonical),
        "idempotency_key_digest_sha256": key_digest,
    }
    request_bytes = REQUEST_DOMAIN + _canonical_json(wrapper)
    statements = [
        "Operation: " + request.authority_operation,
        "Resources: " + ", ".join(request.resources),
        "Expected version: "
        + (str(request.expected_version) if request.expected_version is not None else "new"),
        "Idempotency digest: " + key_digest,
    ]
    # Never truncate approved mutation details. Split only at Unicode scalar
    # boundaries; labels distinguish consecutive parts without changing bytes.
    chunks, chunk, size = [], "", 0
    for char in request.canonical.decode("utf-8"):
        width = len(char.encode("utf-8"))
        if size + width > 480:
            chunks.append(chunk)
            chunk, size = "", 0
        chunk += char
        size += width
    if chunk:
        chunks.append(chunk)
    statements.extend(f"Request part {i}: {part}" for i, part in enumerate(chunks, 1))
    if (
        len(request_bytes) > 131_072
        or len(statements) > 144
        or any(len(item.encode("utf-8")) > 512 or not _display_safe(item) for item in statements)
    ):
        raise ValueError("unsupported_credential_disclosure")
    disclosure = {"title": "Devgraph request", "statements": statements}
    return CredentialRequestBinding(
        request_bytes,
        hashlib.sha256(request_bytes).hexdigest(),
        disclosure,
        hashlib.sha256(_canonical_json(disclosure)).hexdigest(),
    )


def wallet_presentation_request(request_json: bytes, idempotency_key: str, credential: dict):
    binding = credential_request_binding(request_json, idempotency_key)
    return {
        "schema": "castalia.credential-presentation-request.v2",
        "request_bytes_base64": base64.b64encode(binding.request_bytes).decode("ascii"),
        "disclosure": binding.disclosure,
        "credential": credential,
    }
