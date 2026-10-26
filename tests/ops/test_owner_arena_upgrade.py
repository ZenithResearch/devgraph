"""Preparing and endorsing the upgrade cannot change installed authority."""

import base64
import json
from pathlib import Path

import pytest
from tests.ops.test_owner_install import setup as setup_fixture
from tests.ops.test_service_owner import preparation

from devgraph.auth.access_approval import DOMAIN
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.owner_bootstrap import OWNER_DOMAIN, canonical
from devgraph.auth.standing_access import StandingOperatorPolicy
from devgraph.ops import service_owner as owner
from devgraph.ops import wallet_admin_upgrade as bootstrap
from devgraph.ops.owner_arena_upgrade import OwnerArenaUpgrade
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, owner_operator_policy


def configured(tmp_path, monkeypatch):
    s = setup_fixture.__wrapped__(tmp_path, monkeypatch)
    deployment, key, result = preparation(s, monkeypatch)
    plan = owner.plan_path(s.home, deployment["resource_id"])
    digest = result["preparation"]["review_digest"]
    claim = bootstrap.prepare_claim(plan, digest, home=s.home, now=1001)
    proof = dict(
        schema="zenith.resource-owner-proof.v1",
        challenge=claim,
        signature=key.sign(OWNER_DOMAIN + canonical(claim)).hex(),
    )
    bootstrap.complete_claim(
        plan, digest, s.store(s.base / "bootstrap-proof.json", proof), home=s.home, now=1002
    )
    runtime = Path(result["preparation"]["review"]["root"]) / "runtime.json"
    cfg = ReadRuntimeConfiguration.model_validate(json.loads(runtime.read_text()))
    policy = StandingOperatorPolicy.model_validate(owner_operator_policy(cfg, data_root=s.data))
    service = OwnerArenaUpgrade(runtime_file=runtime, data_root=s.data, home=s.home)
    request = dict(
        schema="devgraph.access-operator-action.v10",
        action="upgrade_arena_policy",
        action_id="ab" * 16,
        expected_policy_digest_sha256=policy.digest,
        arena_read_ids=["research"],
    )
    return s, key, cfg, service, request


def test_prepare_endorse_and_retry_leave_all_authority_files_unchanged(tmp_path, monkeypatch):
    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    paths = [
        cfg.access_operator_policy_file,
        cfg.member_policy_file,
        cfg.enrollment_policy_file,
        cfg.authority_policy_file,
        cfg.owner_bootstrap_database_file,
    ]
    before = {path: path.read_bytes() for path in paths}
    public = key.public_key().public_bytes_raw().hex()
    challenge = service.challenge(public, "34" * 16, request, now=None)
    signature = (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )
    response = service.endorse(challenge, signature)
    assert response["status"] == "approved_pending_local_apply" and response["applied"] is False
    assert {path: path.read_bytes() for path in paths} == before
    assert service.challenge(public, "34" * 16, request) == challenge
    assert (
        service.endorse(challenge, signature)["proposal_digest_sha256"]
        == response["proposal_digest_sha256"]
    )


def test_stale_files_wrong_owner_and_tampered_proposal_are_rejected(tmp_path, monkeypatch):
    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        service.challenge("ef" * 32, "34" * 16, request)
    public = key.public_key().public_bytes_raw().hex()
    challenge = service.challenge(public, "34" * 16, request)
    signature = (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )
    cfg.member_policy_file.write_bytes(cfg.member_policy_file.read_bytes() + b" ")
    with pytest.raises(ValueError):
        service.endorse(challenge, signature)


def signed_upgrade(service, key, request):
    challenge = service.challenge(key.public_key().public_bytes_raw().hex(), "34" * 16, request)
    signature = (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )
    service.endorse(challenge, signature)
    return challenge, signature


def test_apply_is_durable_preserves_owner_and_assignments_and_is_idempotent(tmp_path, monkeypatch):
    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    before_members = json.loads(cfg.member_policy_file.read_text())["assignments"]
    review_before = cfg.owner_install_review_file.read_bytes()
    bootstrap_before = cfg.owner_bootstrap_database_file.read_bytes()
    signed_upgrade(service, key, request)
    result = service.apply(request["action_id"])
    assert result["applied"] and result["member_grants_changed"] is False
    new_config = ReadRuntimeConfiguration.model_validate(
        json.loads(service.runtime_file.read_text())
    )
    actual = owner_operator_policy(new_config, data_root=s.data)
    assert actual["ceiling"]["read_grants"][-1]["arena_ids"] == ["research"]
    assert json.loads(cfg.member_policy_file.read_text())["assignments"] == before_members
    assert cfg.owner_install_review_file.read_bytes() == review_before
    assert cfg.owner_bootstrap_database_file.read_bytes() == bootstrap_before
    assert service.apply(request["action_id"]) == result
    assert list((service._directory(request["action_id"]) / "prior").glob("*.json"))


def test_interrupted_apply_recovers_only_original_signed_targets(tmp_path, monkeypatch):
    from devgraph.ops import owner_install as files

    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    signed_upgrade(service, key, request)
    replace = files.replace
    calls = []

    def interrupt(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 3:
            raise OSError("simulated interruption")
        return replace(*args, **kwargs)

    monkeypatch.setattr(files, "replace", interrupt)
    with pytest.raises(OSError):
        service.apply(request["action_id"])
    monkeypatch.setattr(files, "replace", replace)
    assert service.apply(request["action_id"])["applied"]


def test_concurrent_member_change_and_modified_signed_plan_stop_before_mutation(
    tmp_path, monkeypatch
):
    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    signed_upgrade(service, key, request)
    cfg.member_policy_file.write_bytes(cfg.member_policy_file.read_bytes() + b" ")
    prior = cfg.access_operator_policy_file.read_bytes()
    with pytest.raises(ValueError):
        service.apply(request["action_id"])
    assert cfg.access_operator_policy_file.read_bytes() == prior
    p = service._directory(request["action_id"]) / "proposal.json"
    changed = json.loads(p.read_text())
    changed["changes"][0]["after"]["revision"] = 900
    p.write_bytes(canonical_json(changed))
    with pytest.raises(ValueError):
        service.apply(request["action_id"])
    assert cfg.access_operator_policy_file.read_bytes() == prior


def test_status_requires_original_owner_installation_and_verifies_proof(tmp_path, monkeypatch):
    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    signed_upgrade(service, key, request)
    public = key.public_key().public_bytes_raw().hex()
    action = dict(
        schema="devgraph.access-operator-action.v10",
        action="arena_policy_status",
        action_id="de" * 16,
        original_action_id=request["action_id"],
    )
    with pytest.raises(ValueError):
        service.status_challenge("ef" * 32, "34" * 16, action)
    ch = service.status_challenge(public, "34" * 16, action)
    sig = base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(ch))).rstrip(b"=").decode()
    assert service.status(ch, sig)["applied"]
    with pytest.raises(UnauthenticatedError):
        service.status(ch, "A" * 86)


def test_all_registered_access_points_keep_same_ceiling_and_separate_member_records(
    tmp_path, monkeypatch
):
    import copy

    s, key, parent, service, request = configured(tmp_path, monkeypatch)
    primary_path = service.runtime_file
    raw = json.loads(primary_path.read_text())
    origin = "https://192.168.10.20:9443"
    raw.update(
        schema="devgraph.signed-read-runtime.v10", owner_parent_runtime_file=str(primary_path)
    )
    profile = copy.deepcopy(json.loads(parent.owner_install_review_file.read_text())["connection"])
    profile.update(receiver=origin, network="lan", tls_certificate_sha256="a" * 64)
    profile["authority"]["receiver"] = origin
    other = s.base / "published"
    raw["owner_endpoint_profile_file"] = str(s.store(other / "profile.json", profile))
    for field in (
        "access_operator_policy_file",
        "member_policy_file",
        "enrollment_policy_file",
        "authority_policy_file",
    ):
        value = copy.deepcopy(json.loads(Path(raw[field]).read_text()))
        (
            value["ceiling"]
            if field in ("access_operator_policy_file", "member_policy_file")
            else value
        )["receiver"] = origin
        raw[field] = str(s.store(other / (field + ".json"), value))
    for field in (
        "enrollment_database_file",
        "access_inbox_database_file",
        "owner_native_admission_directory",
    ):
        raw[field] = str(other / field)
    child_path = s.store(other / "runtime.json", raw)
    s.store(
        s.home / ".zenith/rpc/v1/fixture.json",
        dict(
            resource_id=json.loads(parent.owner_install_review_file.read_text())["resource_id"],
            runtime=str(child_path),
        ),
    )
    public = key.public_key().public_bytes_raw().hex()
    ch = service.challenge(public, "34" * 16, request)
    assert ch["details"]["action"]["affected_receivers"] == sorted(
        [origin, json.loads(parent.enrollment_policy_file.read_text())["receiver"]]
    )
    sig = base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(ch))).rstrip(b"=").decode()
    service.endorse(ch, sig)
    service.apply(request["action_id"])
    current = ReadRuntimeConfiguration.model_validate(json.loads(primary_path.read_text()))
    child = ReadRuntimeConfiguration.model_validate(json.loads(child_path.read_text()))
    expected = owner_operator_policy(current, data_root=s.data)
    actual = owner_operator_policy(child, data_root=s.data)
    expected["ceiling"]["receiver"] = origin
    assert actual == expected
    assert child.enrollment_database_file != current.enrollment_database_file
    assert child.member_policy_file != current.member_policy_file
    assert child.owner_arena_upgrade_file == current.owner_arena_upgrade_file


def test_partial_transaction_retains_native_inspection_and_status_recovery(tmp_path, monkeypatch):
    from devgraph.ops import owner_install as files
    from devgraph.ops.owner_arena_upgrade import recover_local, recovery_inspection

    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    signed_upgrade(service, key, request)
    replace = files.replace

    def interrupt(path, *args, **kwargs):
        if path == cfg.member_policy_file:
            raise OSError("interrupted")
        return replace(path, *args, **kwargs)

    monkeypatch.setattr(files, "replace", interrupt)
    with pytest.raises(OSError):
        service.apply(request["action_id"])
    config = dict(runtime=str(service.runtime_file), data_root=str(s.data))
    assert recovery_inspection(config)["owner_policy_recovery_required"] is True
    public = key.public_key().public_bytes_raw().hex()
    action = dict(
        schema="devgraph.access-operator-action.v10",
        action="arena_policy_status",
        action_id="ef" * 16,
        original_action_id=request["action_id"],
    )
    ch = recover_local(
        config,
        dict(
            operation="challenge",
            body=dict(subject_public_key=public, installation_id="34" * 16, action=action),
        ),
    )
    sig = base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(ch))).rstrip(b"=").decode()
    monkeypatch.setattr(files, "replace", replace)
    result = recover_local(
        config, dict(operation="execute", body=dict(challenge=ch, signature=sig))
    )
    assert result["result"]["applied"] is True
    assert recovery_inspection(config) is None


def test_owner_all_current_future_role_does_not_widen_member_assignments(tmp_path, monkeypatch):
    from devgraph.auth.access_policy import require_subset
    from devgraph.auth.arena_access import ArenaAccessDraft, OwnerArenaMemberPolicy

    s, key, cfg, service, request = configured(tmp_path, monkeypatch)
    request.update(arena_scope="all_current_and_future", create_arenas=True)
    signed_upgrade(service, key, request)
    assert service.apply(request["action_id"])["applied"]
    current = ReadRuntimeConfiguration.model_validate(json.loads(service.runtime_file.read_text()))
    policy = owner_operator_policy(current, data_root=s.data)
    assert policy["ceiling"]["read_grants"][-1] == dict(
        scope="devgraph.arena.read", all_arenas=True
    )
    member = OwnerArenaMemberPolicy.model_validate(json.loads(cfg.member_policy_file.read_text()))
    assert not member.assignments
    child = dict(
        member.ceiling.model_dump(mode="json", by_alias=True),
        schema="devgraph.access-draft.v6",
        read_grants=[dict(scope="devgraph.arena.read", arena_ids=["future-arena"])],
        write_grants=[],
    )
    require_subset(ArenaAccessDraft.model_validate(child), member.ceiling)
    # An owner-only all-Arena rule cannot become a member credential.
    with pytest.raises(ValueError):
        ArenaAccessDraft.model_validate(policy["ceiling"])


def test_status_closes_unendorsed_action_before_any_late_original_execution(tmp_path, monkeypatch):
    _, key, _, service, request = configured(tmp_path, monkeypatch)
    owner = key.public_key().public_bytes_raw().hex()
    original = service.challenge(owner, "34" * 16, request)
    action = dict(
        schema="devgraph.access-operator-action.v10",
        action="arena_policy_status",
        action_id="78" * 16,
        original_action_id=request["action_id"],
    )
    challenge = service.status_challenge(owner, "34" * 16, action)
    signature = (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(challenge))).rstrip(b"=").decode()
    )
    assert service.status(challenge, signature)["status"] == "not_applied"
    original_signature = (
        base64.urlsafe_b64encode(key.sign(DOMAIN + canonical_json(original))).rstrip(b"=").decode()
    )
    with pytest.raises(ValueError):
        service.endorse(original, original_signature)


def test_restart_waits_for_removed_job_and_retries_only_exact_absent_target(tmp_path, monkeypatch):
    import hashlib
    import subprocess

    path = tmp_path / "fixture.plist"
    path.write_bytes(b"reviewed fixture")
    job = dict(
        path=str(path),
        label="ca.zenith.devgraph.fixture",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    codes = iter([0, 0, 113, 5, 113, 0])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, next(codes), b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    OwnerArenaUpgrade._restart(job)
    starts = [command for command in calls if command[1] == "bootstrap"]
    assert len(starts) == 2
    assert starts[0] == starts[1]
    assert starts[0][-1] == str(path)


def test_restart_never_replaces_unexpected_loaded_job_after_bootstrap_error(tmp_path, monkeypatch):
    import hashlib
    import subprocess

    path = tmp_path / "fixture.plist"
    path.write_bytes(b"reviewed fixture")
    job = dict(
        path=str(path),
        label="ca.zenith.devgraph.fixture",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    codes = iter([0, 113, 5, 0])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, next(codes), b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="restart unconfirmed"):
        OwnerArenaUpgrade._restart(job)
    assert len([command for command in calls if command[1] == "bootstrap"]) == 1
