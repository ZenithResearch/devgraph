import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.auth.owner_bootstrap import (
    OWNER_DOMAIN,
    BootstrapBinding,
    BootstrapError,
    BootstrapStore,
    canonical,
)


def bound_store(path, binding):
    return BootstrapStore(path, binding, current_binding=lambda: binding)


@pytest.fixture
def setup(tmp_path):
    binding = BootstrapBinding(
        resource_id="11111111-2222-4333-8444-555555555555",
        endpoint="http://127.0.0.1:8080",
        configuration_sha256="a" * 64,
        registration_sha256="b" * 64,
    )
    key = Ed25519PrivateKey.generate()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    secret = "c" * 64
    store = bound_store(tmp_path / "owner-state.sqlite3", binding)
    return store, binding, key, public, secret


def arm_and_prepare(setup):
    store, _binding, _key, public, secret = setup
    store.arm(secret, now=100, expires_at=700)
    challenge = store.prepare_claim(secret, public, "d" * 32, now=101)
    return challenge


def proof(key, challenge):
    return {
        "schema": "zenith.resource-owner-proof.v1",
        "challenge": challenge,
        "signature": key.sign(OWNER_DOMAIN + canonical(challenge)).hex(),
    }


def test_setup_authority_is_consumed_atomically_and_replay_returns_original(setup):
    store, _binding, key, public, secret = setup
    challenge = arm_and_prepare(setup)
    signed = proof(key, challenge)
    first = store.redeem(secret, signed, now=102)
    assert first["active"] and first["owner_public_key"] == public
    assert first["admin_grant"]["operations"] == [
        "access.inbox",
        "access.review",
        "access.decide",
        "access.status",
    ]
    assert first["admin_grant"]["validity"]["mode"] == "until_revoked"
    assert first["admin_grant"]["onward_delegation"] is False
    assert store.status("d" * 32)["state"] == "active"
    again = store.redeem(secret, signed, now=103)
    assert again["admin_grant"] == first["admin_grant"] and again["duplicate"]
    with pytest.raises(BootstrapError, match="already_initialized"):
        store.arm("e" * 64, now=103, expires_at=703)
    with pytest.raises(BootstrapError, match="already_initialized"):
        store.prepare_claim(secret, public, "f" * 32, now=103)
    raw = store.path.read_bytes()
    assert secret.encode() not in raw
    assert store.count_grants() == 1


def test_wrong_secret_wallet_or_binding_cannot_claim(setup):
    store, _binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)
    with pytest.raises(BootstrapError):
        store.redeem("e" * 64, proof(key, challenge), now=102)
    with pytest.raises(BootstrapError):
        store.redeem(secret, proof(Ed25519PrivateKey.generate(), challenge), now=102)
    for field, value in [
        ("resource_id", "a378a8ef-95ab-409a-9996-d43163df41fb"),
        ("endpoint", "http://127.0.0.1:8181"),
        ("configuration_sha256", "e" * 64),
    ]:
        changed = json.loads(json.dumps(challenge))
        changed["binding"][field] = value
        with pytest.raises(BootstrapError):
            store.redeem(secret, proof(key, changed), now=102)
    assert store.count_grants() == 0
    assert store.status("d" * 32)["state"] == "prepared"


def test_unknown_authority_and_expired_claim_fail_closed(setup):
    store, _binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)
    changed = json.loads(json.dumps(challenge))
    changed["admin_grant"]["onward_delegation"] = True
    with pytest.raises(BootstrapError):
        store.redeem(secret, proof(key, changed), now=102)
    changed = json.loads(json.dumps(challenge))
    changed["admin_grant"]["operations"].append("work.patch")
    with pytest.raises(BootstrapError):
        store.redeem(secret, proof(key, changed), now=102)
    with pytest.raises(BootstrapError, match="expired"):
        store.redeem(secret, proof(key, challenge), now=221)
    assert store.count_grants() == 0


def test_preparation_is_stable_and_another_claim_cannot_replace_it(setup):
    store, _binding, _key, public, secret = setup
    challenge = arm_and_prepare(setup)
    assert store.prepare_claim(secret, public, "d" * 32, now=102) == challenge
    with pytest.raises(BootstrapError, match="claim_pending"):
        store.prepare_claim(secret, public, "e" * 32, now=102)
    assert store.count_grants() == 0


def test_concurrent_redemption_creates_one_admin(setup):
    store, binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)
    signed = proof(key, challenge)

    def redeem(_):
        return bound_store(store.path, binding).redeem(secret, signed, now=102)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, range(2)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert len({result["admin_grant"]["grant_id"] for result in results}) == 1
    assert store.count_grants() == 1


def test_failed_commit_does_not_burn_authorization_or_activate_admin(setup):
    store, _binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)

    def fail():
        raise RuntimeError("simulated commit failure")

    store.before_commit = fail
    with pytest.raises(RuntimeError):
        store.redeem(secret, proof(key, challenge), now=102)
    assert store.count_grants() == 0
    assert store.status("d" * 32)["state"] == "prepared"
    store.before_commit = None
    assert store.redeem(secret, proof(key, challenge), now=103)["active"]


def test_consumed_authorization_cannot_register_a_different_wallet(setup):
    store, _binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)
    store.redeem(secret, proof(key, challenge), now=102)
    other = Ed25519PrivateKey.generate()
    changed = json.loads(json.dumps(challenge))
    changed["admin_grant"]["holder_public_key"] = (
        other.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    with pytest.raises(BootstrapError):
        store.redeem(secret, proof(other, changed), now=103)
    assert store.count_grants() == 1


def test_uninitialized_and_expired_setup_are_closed(setup):
    store, _binding, _key, public, secret = setup
    assert store.status()["state"] == "uninitialized"
    with pytest.raises(BootstrapError):
        store.prepare_claim(secret, public, "d" * 32, now=100)
    store.arm(secret, now=100, expires_at=200)
    with pytest.raises(BootstrapError, match="expired"):
        store.prepare_claim(secret, public, "d" * 32, now=200)
    assert store.count_grants() == 0


def test_explicit_refresh_keeps_grant_and_deadline_but_invalidates_old_proof(setup):
    store, _binding, key, public, secret = setup
    original = arm_and_prepare(setup)
    refreshed = store.refresh_claim(secret, public, "d" * 32, now=230)
    assert refreshed["admin_grant"] == original["admin_grant"]
    assert refreshed["nonce"] != original["nonce"]
    assert refreshed["expires_at"] == 350
    with pytest.raises(BootstrapError):
        store.redeem(secret, proof(key, original), now=231)
    assert store.redeem(secret, proof(key, refreshed), now=231)["active"]
    with pytest.raises(BootstrapError, match="already_initialized"):
        store.refresh_claim(secret, public, "d" * 32, now=232)


def test_binding_reopen_and_store_permissions_fail_closed(setup, tmp_path):
    import os
    from dataclasses import replace

    store, binding, _key, _public, _secret = setup
    with pytest.raises(BootstrapError, match="binding_or_state_changed"):
        bound_store(store.path, replace(binding, endpoint="http://127.0.0.1:8181"))
    os.chmod(store.path, 0o644)
    with pytest.raises(BootstrapError, match="unsafe_store_permissions"):
        bound_store(store.path, binding)
    os.chmod(store.path, 0o600)
    link = tmp_path / "link.sqlite"
    link.symlink_to(store.path)
    with pytest.raises(BootstrapError):
        bound_store(link, binding)
    link.unlink()
    os.link(store.path, link)
    with pytest.raises(BootstrapError, match="unsafe_store_permissions"):
        bound_store(link, binding)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://graph.example",
        "http://192.168.1.77:8080",
        "https://user:password@graph.example",
        "https://graph.example/path",
        "https://graph.example?x=1",
        "https://graph.example#fragment",
        "https://graph.example:0",
        "https://graph.example:443",
        "https://Graph.example",
        "https://graph.example\\evil",
        "https://graph example",
    ],
)
def test_invalid_or_insecure_binding_rejected(setup, endpoint):
    from dataclasses import replace

    with pytest.raises(BootstrapError, match="invalid_binding"):
        replace(setup[1], endpoint=endpoint)


def test_current_binding_is_rechecked_before_commit_and_stale_claim_rolls_back(setup):
    from dataclasses import replace

    store, binding, key, _public, secret = setup
    challenge = arm_and_prepare(setup)
    current = [binding]
    store.current_binding = lambda: current[0]

    def drift():
        current[0] = replace(binding, configuration_sha256="e" * 64)

    store.before_commit = drift
    with pytest.raises(BootstrapError, match="binding_changed"):
        store.redeem(secret, proof(key, challenge), now=102)
    current[0] = binding
    store.before_commit = None
    assert store.count_grants() == 0
    assert store.status()["state"] == "prepared"
    assert store.redeem(secret, proof(key, challenge), now=103)["active"]
