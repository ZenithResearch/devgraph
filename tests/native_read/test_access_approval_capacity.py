"""Synthetic operator size qualification; signatures and member bounds stay closed."""

import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_access_operator import signed

from devgraph.auth.access_approval import WalletAccessChallenge, verify_wallet_approval
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError


def test_multi_root_operator_consent_is_signed_exactly_without_member_budget_expansion():
    key = Ed25519PrivateKey.generate()
    paths = [
        dict(
            schema="devgraph.resource-path.v1",
            path=[f"Arena/area-{i:02}", f"Initiative/initiative-{i:02}", f"Project/project-{i:02}"],
        )
        for i in range(16)
    ]
    challenge = dict(
        schema="castalia.access-request-challenge.v1",
        challenge_id="a" * 32,
        nonce="A" * 43,
        issuer="fixture",
        receiver="https://example.test",
        subject_public_key=key.public_key().public_bytes_raw().hex(),
        installation_id="b" * 32,
        operation="access.review",
        issued_at=100,
        expires_at=220,
        details={
            "action": {
                "selection": {
                    "grants": [
                        dict(operation=f"test.operation.{i}", resource_paths=paths)
                        for i in range(12)
                    ]
                }
            }
        },
    )
    assert len(canonical_json(challenge)) > 16384
    WalletAccessChallenge.model_validate(challenge)
    signature = signed(key, challenge)
    assert (
        verify_wallet_approval(
            challenge, signature, issuer="fixture", receiver="https://example.test", current=101
        ).details
        == challenge["details"]
    )
    changed = copy.deepcopy(challenge)
    changed["details"]["action"]["selection"]["grants"][0]["resource_paths"].pop()
    with pytest.raises(UnauthenticatedError):
        verify_wallet_approval(
            changed, signature, issuer="fixture", receiver="https://example.test", current=101
        )
    for operation in ["access.request", "access.status"]:
        with pytest.raises(ValueError):
            WalletAccessChallenge.model_validate(dict(challenge, operation=operation))
    with pytest.raises(ValueError):
        WalletAccessChallenge.model_validate(dict(challenge, details={"payload": "x" * 131072}))
