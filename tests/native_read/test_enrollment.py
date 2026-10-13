from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_read import EnrollmentPolicy, NativeReadEnrollment, signable_challenge

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def test_possession_policy_replay_and_revocation(tmp_path: Path):
    owner = Ed25519PrivateKey.generate()
    public = owner.public_key().public_bytes_raw().hex()
    policy = EnrollmentPolicy.model_validate(
        {
            "schema": "devgraph.native-read-policy.v1",
            "issuer": "fixture",
            "receiver": "https://example.test",
            "grants": [
                {
                    "scope": "devgraph.work.read",
                    "operations": ["list", "get"],
                    "relationship_types": ["parent"],
                    "work_kinds": ["Task"],
                    "include_archived": False,
                }
            ],
            "lifetime_seconds": 3600,
        }
    )
    service = NativeReadEnrollment(
        path=tmp_path / "enrollment.sqlite",
        policy=lambda: policy,
        membership_check=lambda proof, key: proof == {"fixture_owner": key},
    )
    challenge = service.challenge(public, "a" * 32, now=NOW)
    with pytest.raises(UnauthenticatedError):
        service.enroll(challenge, {"fixture_owner": public}, "A" * 86, now=NOW)
    import base64

    signature = (
        base64.urlsafe_b64encode(owner.sign(signable_challenge(challenge))).rstrip(b"=").decode()
    )
    response = service.enroll(challenge, {"fixture_owner": public}, signature, now=NOW)
    assert (
        response["manifest"]["read_grants"]
        == policy.model_dump(mode="json", exclude_defaults=True)["grants"]
    )
    authority = service.verify(response["credential"], audience="devgraph", now=NOW)
    assert authority.scopes == {"devgraph.work.read"}
    assert "devgraph.write" not in authority.scopes
    assert response["credential"].encode() not in (tmp_path / "enrollment.sqlite").read_bytes()
    with pytest.raises(UnauthenticatedError, match="challenge_used"):
        service.enroll(challenge, {"fixture_owner": public}, signature, now=NOW)
    altered = {**challenge, "receiver": "https://evil.test"}
    with pytest.raises(UnauthenticatedError):
        service.enroll(altered, {"fixture_owner": public}, signature, now=NOW)
    service.revoke(response["manifest"]["credential_id"])
    with pytest.raises(UnauthenticatedError):
        service.verify(response["credential"], audience="devgraph", now=NOW)
    assert signable_challenge(challenge).endswith(canonical_json(challenge))
