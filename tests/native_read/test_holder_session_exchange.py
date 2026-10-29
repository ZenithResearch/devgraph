"""Retained exchange/status with disposable parents and actual native crypto."""

from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_holder_grants import alias, host
from tests.native_read.test_signed_authority import NOW

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_grants import checksum
from devgraph.auth.holder_sessions import HolderSessionHost, HolderSessionPolicy


def setup(tmp_path):
    h, e, owner, args, registry, _, _ = host(tmp_path)
    h.grant_schema = "secs.holder-grant.v2"
    parent = h.issue(**args)["grant"]
    key = Ed25519PrivateKey.generate()
    policy = HolderSessionPolicy.model_validate(
        {"enabled": True, "allowed_origins": ["https://graph.example"]}
    )
    service = HolderSessionHost(h, policy)
    body = {
        "schema": "secs.holder-session-prepare.v1",
        "request_id": "44" * 16,
        "parent_grant": parent,
        "installation_id": "a" * 32,
        "session_installation_id": "b" * 32,
        "session_public_key": key.public_key().public_bytes_raw().hex(),
        "origin": "https://graph.example",
        "audience": "devgraph",
        "permissions": None,
        "requested_ttl_seconds": 300,
    }
    challenge = service.prepare(body, now=NOW)["challenge"]
    authorization = {
        "schema": "secs.holder-session-authorization.v1",
        "challenge": challenge,
        "parent_grant": parent,
        "holder_signature": owner.sign(
            b"secs.holder-session-authorization.v1/signature\0" + canonical_json(challenge)
        ).hex(),
        "session_signature": key.sign(
            b"secs.holder-session-possession.v1/signature\0" + canonical_json(challenge)
        ).hex(),
    }
    return service, authorization, key, body, e, registry


def status(key, challenge, nonce="77" * 16, now=NOW):
    current = int(now.timestamp())
    claims = {
        "schema": "secs.holder-session-status.v1",
        "request_id": challenge["request_id"],
        "challenge_id": challenge["challenge_id"],
        "nonce": nonce,
        "issued_at": current,
        "expires_at": min(current + 60, challenge["expires_at"]),
    }
    return dict(
        claims,
        signature=key.sign(
            b"secs.holder-session-status.v1/signature\0" + canonical_json(claims)
        ).hex(),
    )


def read_request(key, credential, nonce="88" * 16, now=NOW, operation="devgraph.work.list"):
    current = int(now.timestamp())
    call = {
        "operation": operation,
        "resource": "https://work.zenith-research.ca/devgraph",
        "parameters": {"path": "/todos/v2?kind=Task"},
    }
    claims = {
        "schema": "secs.holder-session-presentation.v1",
        "credential_digest": checksum(credential),
        "call_digest": checksum(call),
        "nonce": nonce,
        "issued_at": current,
        "expires_at": min(current + 60, credential["claims"]["challenge"]["expires_at"]),
    }
    proof = dict(
        claims,
        signature=key.sign(
            b"secs.holder-session-presentation.v1/signature\0" + canonical_json(claims)
        ).hex(),
    )
    return {"credential": credential, "call": call, "proof": proof}


def test_prepare_and_exchange_recover_exact_original_without_extending_end(tmp_path):
    s, auth, _, body, _, _ = setup(tmp_path)
    assert s.prepare(body, now=NOW)["challenge"] == auth["challenge"]
    issued = s.exchange(auth, now=NOW)
    assert issued["state"] == "issued"
    assert "parent_grant" not in issued["credential"]
    assert issued["expires_at"] == auth["challenge"]["expires_at"]
    # An identical issued retry survives both restart and the review deadline.
    restarted = HolderSessionHost(s.host, s.policy)
    assert restarted.exchange(auth, now=NOW + timedelta(seconds=121)) == issued
    changed = dict(body, requested_ttl_seconds=200)
    with pytest.raises(UnauthenticatedError, match="conflict"):
        s.prepare(changed, now=NOW)


def test_late_new_exchange_and_clock_rollback_do_not_issue_or_recover_authority(tmp_path):
    s, auth, _, _, _, _ = setup(tmp_path)
    assert s.exchange(auth, now=NOW + timedelta(seconds=120))["state"] == "expired"
    # A confirmed terminal review cannot reopen after a restart or clock rollback.
    restarted = HolderSessionHost(s.host, s.policy)
    assert restarted.exchange(auth, now=NOW)["state"] == "expired"
    with pytest.raises(UnauthenticatedError):
        s.exchange(auth, now=NOW - timedelta(seconds=1))


def test_status_requires_temporary_possession_and_never_issues_a_credential(tmp_path, monkeypatch):
    s, auth, key, _, _, _ = setup(tmp_path)
    challenge = auth["challenge"]
    bad = status(Ed25519PrivateKey.generate(), challenge)
    original = s._parent

    def forbidden(*_args):
        raise AssertionError("invalid possession cannot inspect current parent")

    monkeypatch.setattr(s, "_parent", forbidden)
    with pytest.raises(UnauthenticatedError):
        s.status(bad, now=NOW)
    monkeypatch.setattr(s, "_parent", original)
    proof = status(key, challenge)
    result = s.status(proof, now=NOW)
    assert result["state"] == "pending"
    assert result["credential"] is None
    assert result["expires_at"] == challenge["expires_at"]
    with pytest.raises(UnauthenticatedError, match="replay"):
        s.status(proof, now=NOW)


def test_canonical_parent_revocation_crosses_aliases_and_status_returns_no_token(tmp_path):
    s, auth, key, _, provider, registry = setup(tmp_path)
    issued = s.exchange(auth, now=NOW)
    alias_host, _ = alias(tmp_path / "alias", provider, registry)
    remote = HolderSessionHost(alias_host, s.policy)
    assert remote.exchange(auth, now=NOW) == issued
    provider.revoke("dgrg_" + auth["challenge"]["parent_grant_id"])
    result = remote.status(status(key, auth["challenge"]), now=NOW)
    assert result["state"] == "revoked"
    assert result["credential"] is None
    assert result["expires_at"] == issued["expires_at"]
    with pytest.raises(UnauthenticatedError):
        s.exchange(auth, now=NOW)


def test_exchange_requires_exact_retained_challenge_and_both_signatures(tmp_path):
    s, auth, _, _, _, _ = setup(tmp_path)
    for field in ("holder_signature", "session_signature"):
        bad = dict(auth, **{field: "00" * 64})
        with pytest.raises(UnauthenticatedError):
            s.exchange(bad, now=NOW)
    with s.host.provider._database() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM holder_sessions WHERE credential IS NOT NULL"
        ).fetchone() == (0,)
    assert s.exchange(auth, now=NOW)["state"] == "issued"


def test_expired_original_result_cannot_return_a_usable_token(tmp_path):
    s, auth, _, _, _, _ = setup(tmp_path)
    issued = s.exchange(auth, now=NOW)
    result = s.exchange(auth, now=NOW + timedelta(seconds=300))
    assert result["state"] == "expired"
    assert result["credential"] is None
    assert result["expires_at"] == issued["expires_at"]


def test_normal_read_uses_temporary_key_current_parent_and_canonical_replay(tmp_path):
    s, auth, key, _, provider, registry = setup(tmp_path)
    token = s.exchange(auth, now=NOW)["credential"]
    body = read_request(key, token)
    with s.from_origin("https://graph.example"):
        result = provider.verify_request(**body, now=NOW)
        assert result.read_grants
        assert result.scopes == frozenset({"devgraph.work.read"})
        with s.host.current_read(token, body["proof"], now=NOW):
            pass
    other, remote = alias(tmp_path / "alias", provider, registry)
    remote_sessions = HolderSessionHost(other, s.policy)
    with remote_sessions.from_origin("https://graph.example"):
        with pytest.raises(UnauthenticatedError, match="replay"):
            remote.verify_request(**body, now=NOW)
    provider.revoke("dgrg_" + auth["challenge"]["parent_grant_id"])
    with s.from_origin("https://graph.example"):
        with pytest.raises(UnauthenticatedError):
            with s.host.current_read(token, body["proof"], now=NOW):
                pytest.fail("revoked parent cannot disclose a result")
        with pytest.raises(UnauthenticatedError):
            provider.verify_request(**read_request(key, token, nonce="99" * 16), now=NOW)


@pytest.mark.parametrize("variant", ["missing_origin", "other_origin", "wrong_key", "write"])
def test_normal_read_rejects_unobserved_origin_wrong_key_and_write(tmp_path, variant):
    s, auth, key, _, provider, _ = setup(tmp_path)
    token = s.exchange(auth, now=NOW)["credential"]
    origin = (
        None
        if variant == "missing_origin"
        else ("https://other.example" if variant == "other_origin" else "https://graph.example")
    )
    body = read_request(
        Ed25519PrivateKey.generate() if variant == "wrong_key" else key,
        token,
        operation="devgraph.work.patch" if variant == "write" else "devgraph.work.list",
    )
    with pytest.raises(UnauthenticatedError):
        with s.from_origin(origin):
            provider.verify_request(**body, now=NOW)


def test_policy_change_during_issuance_prevents_commit_and_token_disclosure(tmp_path, monkeypatch):
    s, auth, _, _, _, _ = setup(tmp_path)
    current = [True]
    s.policy_check = lambda: current[0]
    original = s.host.invoke

    def invoke(operation, **fields):
        result = original(operation, **fields)
        if operation == "issue_holder_session":
            current[0] = False
        return result

    monkeypatch.setattr(s.host, "invoke", invoke)
    with pytest.raises(UnauthenticatedError, match="policy_changed"):
        s.exchange(auth, now=NOW)
    with s.host.provider._database() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM holder_sessions WHERE credential IS NOT NULL"
        ).fetchone() == (0,)
