"""An existing owner must endorse the exact additive Arena policy change."""

import base64
import hashlib
import secrets

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_path_access_flow import setup
from tests.native_read.test_standing_access import standing

from devgraph.auth.access_approval import DOMAIN
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.owner_arena_policy import upgraded_policy, verify_endorsement
from devgraph.auth.standing_access import StandingOperatorPolicy


def fixture(tmp_path):
    state = setup(tmp_path)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw().hex()
    raw = state.policies[0].model_dump(mode="json", by_alias=True, exclude_defaults=True)
    raw.update(
        schema="devgraph.access-operator-policy.v4",
        operator_public_keys=[public],
        ceiling=standing(state.base[0].ceiling).model_dump(
            mode="json", by_alias=True, exclude_defaults=True
        ),
    )
    raw["ceiling"]["validity"]["not_before"] = 1
    policy = StandingOperatorPolicy.model_validate(raw)
    original = policy.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    review = dict(
        resource_id="5b4e45ac-6e51-43ee-aad0-f1818a43f252",
        owner_public_key=public,
        staged={"authority/operators.json": hashlib.sha256(canonical_json(original)).hexdigest()},
    )
    changed = upgraded_policy(original, ["research"])
    action = dict(
        schema="devgraph.access-operator-action.v10",
        action="upgrade_arena_policy",
        action_id="ab" * 16,
        resource_id=review["resource_id"],
        owner_grant_id=hashlib.sha256(canonical_json(review)).hexdigest(),
        primary_receiver=policy.ceiling.receiver,
        selected_receiver=policy.ceiling.receiver,
        affected_receivers=[policy.ceiling.receiver],
        previous_primary_policy_digest_sha256=policy.digest,
        previous_selected_policy_digest_sha256=policy.digest,
        new_primary_policy_digest_sha256=changed.digest,
        arena_read_ids=["research"],
        proposal_digest_sha256="cd" * 32,
    )
    challenge = dict(
        schema="castalia.access-request-challenge.v1",
        challenge_id="12" * 16,
        nonce=secrets.token_urlsafe(32),
        issuer=policy.ceiling.issuer,
        receiver=policy.ceiling.receiver,
        subject_public_key=public,
        installation_id="34" * 16,
        issued_at=1000,
        expires_at=1120,
        operation="access.decide",
        details=dict(
            action=action, policy_revision=policy.revision, policy_digest_sha256=policy.digest
        ),
    )
    proof = dict(
        schema="devgraph.owner-arena-endorsement.v1",
        original_policy=original,
        challenge=challenge,
        signature=base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge)))
        .rstrip(b"=")
        .decode(),
        approved_at=1001,
    )
    return policy, changed, review, proof


def test_endorsement_preserves_every_existing_authority_component(tmp_path):
    old, new, review, proof = fixture(tmp_path)
    assert (
        verify_endorsement(
            proof,
            review=review,
            actual_policy=new.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        )
        == new
    )
    assert old.operator_public_keys == new.operator_public_keys
    assert old.ceiling.write_grants == new.ceiling.write_grants
    assert old.ceiling.read_grants == new.ceiling.read_grants[:-1]
    assert old.ceiling.validity == new.ceiling.validity
    assert old.ceiling.lifetime_seconds == new.ceiling.lifetime_seconds
    assert new.ceiling.read_grants[-1].arena_ids == ("research",)


@pytest.mark.parametrize(
    "change", ["key", "resource", "grant", "arena", "prior", "future", "expiry", "policy"]
)
def test_wrong_or_stale_endorsement_never_rebinds_owner(tmp_path, change):
    old, new, review, proof = fixture(tmp_path)
    raw = new.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    if change == "key":
        proof["challenge"]["subject_public_key"] = "ef" * 32
    elif change == "resource":
        proof["challenge"]["details"]["action"]["resource_id"] = (
            "00000000-0000-0000-0000-000000000000"
        )
    elif change == "grant":
        proof["challenge"]["details"]["action"]["owner_grant_id"] = "ef" * 32
    elif change == "arena":
        proof["challenge"]["details"]["action"]["arena_read_ids"] = ["private"]
    elif change == "prior":
        proof["original_policy"]["revision"] += 1
    elif change == "future":
        proof["approved_at"] = 999
    elif change == "expiry":
        proof["approved_at"] = 1120
    elif change == "policy":
        raw["ceiling"]["lifetime_seconds"] += 1
    with pytest.raises(ValueError):
        verify_endorsement(proof, review=review, actual_policy=raw)


def test_original_signed_registration_is_retained_after_explicit_endorsement(
    tmp_path, monkeypatch, request
):
    import json
    from pathlib import Path

    from tests.ops.test_owner_install import setup as setup_fixture
    from tests.ops.test_service_owner import preparation

    from devgraph.auth.owner_bootstrap import OWNER_DOMAIN, canonical
    from devgraph.ops import service_owner as owner
    from devgraph.ops import wallet_admin_upgrade as upgrade
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy

    # This fixture uses the real bootstrap store and disposable native host state.
    s = setup_fixture.__wrapped__(tmp_path, monkeypatch)
    deployment, key, result = preparation(s, monkeypatch)
    plan = owner.plan_path(s.home, deployment["resource_id"])
    digest = result["preparation"]["review_digest"]
    claim = upgrade.prepare_claim(plan, digest, home=s.home, now=1001)
    proof = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=claim,
        signature=key.sign(OWNER_DOMAIN + canonical(claim)).hex(),
    )
    upgrade.complete_claim(
        plan, digest, s.store(s.base / "bootstrap-proof.json", proof), home=s.home, now=1002
    )
    runtime = Path(result["preparation"]["review"]["root"]) / "runtime.json"
    cfg = ReadRuntimeConfiguration.model_validate(json.loads(runtime.read_text()))
    original = owner_operator_policy(cfg, data_root=s.data)
    review = json.loads(cfg.owner_install_review_file.read_text())
    changed = upgraded_policy(original, ["research"])
    base = StandingOperatorPolicy.model_validate(original)
    action = dict(
        schema="devgraph.access-operator-action.v10",
        action="upgrade_arena_policy",
        action_id="ab" * 16,
        resource_id=review["resource_id"],
        owner_grant_id=hashlib.sha256(canonical_json(review)).hexdigest(),
        primary_receiver=base.ceiling.receiver,
        selected_receiver=base.ceiling.receiver,
        affected_receivers=[base.ceiling.receiver],
        previous_primary_policy_digest_sha256=base.digest,
        previous_selected_policy_digest_sha256=base.digest,
        new_primary_policy_digest_sha256=changed.digest,
        arena_read_ids=["research"],
        proposal_digest_sha256="cd" * 32,
    )
    challenge = dict(
        schema="castalia.access-request-challenge.v1",
        challenge_id="12" * 16,
        nonce=secrets.token_urlsafe(32),
        issuer=base.ceiling.issuer,
        receiver=base.ceiling.receiver,
        subject_public_key=review["owner_public_key"],
        installation_id="34" * 16,
        issued_at=1003,
        expires_at=1123,
        operation="access.decide",
        details=dict(
            action=action, policy_revision=base.revision, policy_digest_sha256=base.digest
        ),
    )
    endorsement = dict(
        schema="devgraph.owner-arena-endorsement.v1",
        original_policy=original,
        challenge=challenge,
        signature=base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge)))
        .rstrip(b"=")
        .decode(),
        approved_at=1004,
    )
    path = s.store(s.base / "arena-endorsement.json", endorsement)
    state = cfg.owner_bootstrap_database_file.read_bytes()
    values = cfg.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    values.update(schema="devgraph.signed-read-runtime.v11", owner_arena_upgrade_file=str(path))
    next_cfg = ReadRuntimeConfiguration.model_validate(values)
    s.store(
        cfg.access_operator_policy_file,
        changed.model_dump(mode="json", by_alias=True, exclude_defaults=True),
    )
    assert owner_operator_policy(next_cfg, data_root=s.data) == changed.model_dump(
        mode="json", by_alias=True, exclude_defaults=True
    )
    assert cfg.owner_bootstrap_database_file.read_bytes() == state
    with pytest.raises(ValueError):
        owner_operator_policy(cfg, data_root=s.data)
    for schema in ("devgraph.signed-read-runtime.v9", "devgraph.signed-read-runtime.v10"):
        with pytest.raises(ValueError):
            ReadRuntimeConfiguration.model_validate(dict(values, schema=schema))
