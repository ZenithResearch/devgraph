"""Optional matched WASM -> real membership -> Devgraph request qualification."""

import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.auth.access_requests import AccessRequestInbox, RequestAction
from devgraph.auth.native_membership import ZenithMembershipValidator


def test_actual_wallet_core_submits_request_and_status_without_credentials(tmp_path):
    driver = os.environ.get("CASTALIA_ACCESS_REQUEST_FIXTURE_DRIVER")
    if not driver:
        pytest.skip("matched Wallet WASM fixture driver required")

    def core(**payload):
        process = subprocess.run(
            [os.environ.get("CASTALIA_TEST_NODE", "node"), driver],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return json.loads(process.stdout)

    public = core(operation="identity")["subject_public_key"]
    issuer = Ed25519PrivateKey.generate()
    trust = {
        "v3": dict(
            schema="castalia.zenith-membership-trust-policy.v1",
            version=1,
            roots=[
                dict(
                    issuerId="fixture",
                    keyId="fixture-key",
                    signatureSuite="Ed25519",
                    publicKey=issuer.public_key().public_bytes_raw().hex(),
                )
            ],
        )
    }
    proof = dict(
        schema="castalia.zenith-membership-credential.v3",
        version=3,
        membershipId=hashlib.sha256(
            b"castalia/zenith-membership-id/v3\0" + bytes.fromhex(public)
        ).hexdigest(),
        ownerPublicKey=public,
        status="active",
        issuerId="fixture",
        issuerKeyId="fixture-key",
        signatureSuite="Ed25519",
    )
    transcript = (
        b"castalia/zenith-membership-credential/v3\0"
        + (3).to_bytes(8, "little")
        + bytes.fromhex(proof["membershipId"])
        + bytes.fromhex(public)
    )
    for text in ("active", "fixture", "fixture-key", "ed25519"):
        transcript += len(text.encode()).to_bytes(4, "little") + text.encode()
    proof["issuerSignature"] = (
        base64.urlsafe_b64encode(issuer.sign(transcript)).rstrip(b"=").decode()
    )
    revoked = set()
    validator = ZenithMembershipValidator(lambda: trust, lambda mid: mid in revoked)
    assert validator(proof, public)
    inbox = AccessRequestInbox(
        path=tmp_path / "wallet-inbox.sqlite",
        issuer="fixture",
        receiver="https://example.test",
        membership_check=validator,
    )
    action = RequestAction.model_validate(
        dict(
            schema="devgraph.access-request-action.v1",
            action="submit",
            request_id="c" * 32,
            subject_public_key=public,
            installation_id="b" * 32,
            intent=None,
        )
    )
    challenge = inbox.wallet_challenge(action)
    approval = core(operation="approve", challenge=challenge, now=int(time.time()))
    result = inbox.execute_wallet(proof=proof, **approval)
    assert result["status"] == "pending" and result["authority_issued"] is False
    status = inbox.wallet_challenge(action.model_copy(update={"action": "status"}))
    reply = core(operation="approve", challenge=status, now=int(time.time()))
    assert inbox.execute_wallet(proof=proof, **reply) == result
    assert inbox.pending()["total"] == 1
    assert "credential" not in result
    assert proof["issuerSignature"].encode() not in Path(inbox.path).read_bytes()
