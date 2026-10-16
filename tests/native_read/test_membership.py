import json
from copy import deepcopy
from pathlib import Path

import pytest

from devgraph.auth.native_membership import ZenithMembershipValidator


def test_frozen_v3_vector_and_revocation():
    vector = json.loads((Path(__file__).parent / "zenith-v3.vector.json").read_text())
    trust = {
        "v3": {
            "schema": "castalia.zenith-membership-trust-policy.v1",
            "version": 1,
            "roots": [
                {
                    "issuerId": "zenith-research",
                    "keyId": vector["issuerKeyId"],
                    "signatureSuite": "Ed25519",
                    "publicKey": vector["issuerPublicKey"],
                }
            ],
        }
    }
    revoked = set()
    verify = ZenithMembershipValidator(lambda: trust, lambda mid: mid in revoked)
    assert verify(vector["credential"], vector["ownerPublicKey"])
    assert not verify(vector["credential"], "00" * 32)
    revoked.add(vector["membershipId"])
    assert not verify(vector["credential"], vector["ownerPublicKey"])


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "duplicate_scope",
        "invalid_unused_scope",
        "issuer_string",
        "duplicate_issuer",
        "wrong_genesis",
        "unknown_section",
    ],
)
def test_public_typescript_v4_vector_and_strict_trust(corruption):
    vector = json.loads((Path(__file__).parent / "zenith-v4.vector.json").read_text())
    policy = deepcopy(vector["trust"])
    scope = policy["scopes"][0]
    if corruption == "duplicate_scope":
        policy["scopes"].append(deepcopy(scope))
    elif corruption == "invalid_unused_scope":
        policy["scopes"].append({"scopeId": "00" * 32})
    elif corruption == "issuer_string":
        scope["issuerIds"] = vector["credential"]["issuerId"]
    elif corruption == "duplicate_issuer":
        scope["issuerIds"] *= 2
    elif corruption == "wrong_genesis":
        scope["genesis"]["nonce"] = "00" * 32
    elif corruption == "unknown_section":
        scope["privateKey"] = "not allowed"
    verify = ZenithMembershipValidator(lambda: {"v4": policy}, lambda _: False)
    assert verify(vector["credential"], vector["ownerPublicKey"]) is (corruption is None)
