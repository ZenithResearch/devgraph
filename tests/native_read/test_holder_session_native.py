"""Finite-session native crypto adapter; no registry or HTTP authority claim.

The public signed vector is byte-identical to secS contract 8a62adb137bd9f6efbbe
937ff80352c86ad6b1cf, crates/secs-grant-contract/tests/fixtures/holder-session-v1.json.
SHA256: 15537a80cc14ace50d1d47400c53851bf7ba272e1861fd7b7ad051cb8c400046.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import invoke_native


@pytest.fixture
def native():
    value = os.environ.get("SECS_HOLDER_GRANT_TEST_BINARY")
    if not value:
        pytest.skip("actual native holder grant authority required")
    binary = Path(value).resolve(strict=True)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


@pytest.fixture
def vector():
    path = Path(__file__).parents[1] / "fixtures/holder-session-v1.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "15537a80cc14ace50d1d47400c53851bf7ba272e1861fd7b7ad051cb8c400046"
    )
    return json.loads(raw)


@pytest.mark.parametrize(
    ("operation", "names", "schema"),
    [
        (
            "review_holder_session",
            ("parent_grant", "challenge"),
            "secs.holder-session-reviewed.v1",
        ),
        (
            "verify_holder_session_authorization",
            ("authorization",),
            "secs.holder-session-authorization-verified.v1",
        ),
        (
            "verify_holder_session",
            ("credential", "parent_grant", "call", "proof"),
            "secs.verified-holder-session.v1",
        ),
        (
            "verify_holder_session_status",
            ("challenge", "status"),
            "secs.holder-session-status-verified.v1",
        ),
    ],
)
def test_exact_native_session_vector(native, vector, operation, names, schema):
    fields = {"proof" if name == "status" else name: vector[name] for name in names}
    result = invoke_native(
        *native, vector["pin"], operation, context=vector["context"], now=103, **fields
    )
    assert result["schema"] == schema
    if operation == "verify_holder_session":
        assert result["holder_public_key"] != result["session_public_key"]
        assert result["grant_id"] == vector["challenge"]["parent_grant_id"]
        assert result["permissions"] == vector["challenge"]["permissions"]
        assert "parent_grant" not in result


def test_native_session_issuer_uses_only_explicit_disposable_key(native, vector, tmp_path):
    key = tmp_path / "issuer.key"
    key.write_text("3d" * 32)  # Published synthetic vector issuer, never a real key.
    key.chmod(0o600)
    fields = dict(
        authorization=vector["authorization"],
        credential_id=vector["credential"]["claims"]["credential_id"],
        context=vector["context"],
        now=102,
    )
    with pytest.raises(UnauthenticatedError, match="holder_authority_denied"):
        invoke_native(*native, vector["pin"], "issue_holder_session", **fields)
    result = invoke_native(
        *native, vector["pin"], "issue_holder_session", issuer_key_file=key, **fields
    )
    assert result == vector["credential"]


@pytest.mark.parametrize("variant", ["origin", "expiry", "signature", "parent"])
def test_native_session_denies_changed_bindings(native, vector, variant):
    if variant == "origin":
        vector["context"]["origin"] = "https://other.example.invalid"
    elif variant == "signature":
        vector["proof"]["signature"] = "00" * 64
    elif variant == "parent":
        vector["parent_grant"]["claims"]["revision"] = "00" * 32
    with pytest.raises(UnauthenticatedError, match="holder_authority_denied"):
        invoke_native(
            *native,
            vector["pin"],
            "verify_holder_session",
            context=vector["context"],
            now=400 if variant == "expiry" else 103,
            **{name: vector[name] for name in ("credential", "parent_grant", "call", "proof")},
        )
