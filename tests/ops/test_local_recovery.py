from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from devgraph.local_host import LocalHostConfig
from devgraph.ops import local_maintenance as maintenance
from devgraph.ops import local_recovery as recovery


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    for name in ("data", "install", "logs"):
        (tmp_path / name).mkdir(mode=0o700)
    settings = LocalHostConfig.build(data_root=tmp_path / "data", log_root=tmp_path / "logs")
    monkeypatch.setattr(recovery, "INSTALL_ROOT", tmp_path / "install")
    root = settings.data_root / maintenance.BACKUP_RELATIVE
    root.mkdir(parents=True, mode=0o700)
    artifact = root / "20260911T120000Z-000000000001"
    artifact.mkdir(mode=0o700)
    files = {}
    public = "1" * 64
    descriptor = {
        "schema": "secs-devgraph-work-admin.v1",
        "action": "snapshot",
        "ready": True,
        "policy_id": "test-policy",
        "policy_version": 1,
        "policy_digest_sha256": "2" * 64,
        "secs_verifier_key_id": "test-key",
        "registry_sha256": "3" * 64,
    }
    descriptor["policy"] = {
        "rules": [
            {"actor_id": "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()}
        ]
    }
    descriptor["files"] = sorted(recovery._AUTHORITY_FILES)
    descriptor["file_hashes"] = {
        name: {"sha256": hashlib.sha256(b"synthetic").hexdigest(), "size_bytes": 9}
        for name in recovery._AUTHORITY_FILES
    }
    for name, raw in {
        "neo4j.dump": b"fixture-neo4j",
        "system.dump": b"fixture-system",
        "support/secrets/secs-magik/devgraph.work.v1/receiver.json": json.dumps(
            {
                "policy_binding": {
                    "policy_id": "test-policy",
                    "policy_version": 1,
                    "policy_digest_sha256": "2" * 64,
                }
            }
        ).encode(),
        "support/secrets/secs-magik/devgraph.work.v1/secs-public-key-registry.json": b"registry",
    }.items():
        path = artifact / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)
        files[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    descriptor["registry_sha256"] = hashlib.sha256(b"registry").hexdigest()
    maintenance._json_write(
        artifact / "manifest.json",
        {
            "schema": maintenance.SCHEMA,
            "complete": True,
            "artifact_id": artifact.name,
            "completed_at": 100,
            "files": files,
            "migration": 25,
            "source_release": "test-release",
        },
    )
    monkeypatch.setattr(
        recovery,
        "signer_environment",
        lambda: {
            "DEVGRAPH_SIGNING_PUBLIC_KEY": public,
            "DEVGRAPH_SIGNING_KEY_FILE": str(tmp_path / "install/source.key"),
        },
    )
    monkeypatch.setattr(recovery, "wallet_key_operation", lambda mode, path: public)

    def native(relative, args, env=None):
        if "--export-identity" in args:
            path = Path(args[-1])
            path.write_bytes(b"synthetic-key")
            path.chmod(0o600)
            return {
                "schema": "devgraph.wallet-recovery-export.v1",
                "public_key": public,
                "exported": True,
                "application_encrypted": False,
            }
        if "snapshot" in args:
            path = Path(args[-1])
            for name in recovery._AUTHORITY_FILES:
                (path / name).write_bytes(b"synthetic")
                (path / name).chmod(0o600)
        return dict(descriptor)

    monkeypatch.setattr(recovery, "_native", native)
    monkeypatch.setattr(
        recovery,
        "_disk_identity",
        lambda path: {
            "device": 2 if settings.data_root in path.parents or path == settings.data_root else 1,
            "physical_disks": [
                "disk2"
                if settings.data_root in path.parents or path == settings.data_root
                else "disk0"
            ],
            "volume_uuid": "fixture",
        },
    )
    monkeypatch.setattr(
        recovery.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 50 * 1024**3})()
    )
    return settings, artifact, descriptor


def test_complete_recovery_verifies_both_roots_without_reading_custody_seeds(fixture, monkeypatch):
    settings, artifact, _ = fixture
    original = maintenance._private_file

    def public_only(path):
        assert path.name not in {"devgraph-dregg.key", "verifier.key"}
        return original(path)

    monkeypatch.setattr(maintenance, "_private_file", public_only)
    result = recovery.create_recovery(settings, artifact)
    assert result["successful"] is True
    assert len(result["destinations"]) == 2
    verified = recovery.recovery_status(settings, verify=True)
    assert verified["successful"] is True
    for root in recovery.recovery_roots(settings).values():
        latest = json.loads((root / "latest.json").read_text())
        manifest = json.loads((root / latest["artifact_id"] / "manifest.json").read_text())
        assert manifest["authority_restore_state"] == "inactive_pending_current_authority_review"
        assert manifest["application_encrypted"] is False


def test_physical_overlap_rejected_before_key_export(fixture, monkeypatch):
    settings, artifact, _ = fixture
    monkeypatch.setattr(
        recovery,
        "_disk_identity",
        lambda path: {"device": 1, "physical_disks": ["disk0"], "volume_uuid": "x"},
    )
    monkeypatch.setattr(
        recovery,
        "_native",
        lambda *a, **k: pytest.fail("custody operation before destination proof"),
    )
    with pytest.raises(recovery.RecoveryError, match="independent"):
        recovery.create_recovery(settings, artifact)


def test_authority_drift_never_publishes_a_recovery_set(fixture, monkeypatch):
    settings, artifact, _ = fixture
    native = recovery._native
    calls = [0]

    def drift(relative, args, env=None):
        result = native(relative, args, env)
        if "snapshot" in args:
            calls[0] += 1
            if calls[0] > 1:
                result["policy_version"] = 2
        return result

    monkeypatch.setattr(recovery, "_native", drift)
    with pytest.raises(recovery.RecoveryError, match="authority"):
        recovery.create_recovery(settings, artifact)
    assert all(
        not (root / "latest.json").exists() for root in recovery.recovery_roots(settings).values()
    )


def test_copy_corruption_and_symlinks_are_rejected(fixture):
    settings, artifact, _ = fixture
    result = recovery.create_recovery(settings, artifact)
    root = next(iter(recovery.recovery_roots(settings).values()))
    selected = root / result["artifact_id"]
    dump = selected / "database" / artifact.name / "neo4j.dump"
    dump.write_bytes(b"changed")
    assert recovery.recovery_status(settings, verify=True)["successful"] is False
    dump.unlink()
    dump.symlink_to(artifact / "neo4j.dump")
    assert recovery.recovery_status(settings, verify=True)["successful"] is False


def test_status_is_metadata_only_and_missing_is_not_success(fixture, monkeypatch):
    settings, artifact, _ = fixture
    assert recovery.recovery_status(settings)["successful"] is False
    recovery.create_recovery(settings, artifact)
    monkeypatch.setattr(
        recovery, "_native", lambda *a, **k: pytest.fail("status must not open custody")
    )
    assert recovery.recovery_status(settings)["successful"] is True


@pytest.mark.parametrize("fault", ["identity", "authority-files", "extra-file", "traversal"])
def test_verification_rejects_changed_identity_authority_and_inventory(fixture, monkeypatch, fault):
    settings, artifact, descriptor = fixture
    result = recovery.create_recovery(settings, artifact)
    root = recovery.recovery_roots(settings)["internal"]
    selected = root / result["artifact_id"]
    if fault == "identity":
        monkeypatch.setattr(recovery, "wallet_key_operation", lambda *a: "4" * 64)
    elif fault == "authority-files":
        descriptor["file_hashes"]["replay.sqlite3"] = {
            "sha256": "4" * 64,
            "size_bytes": 42,
        }
    elif fault == "extra-file":
        maintenance._write(selected / "unexpected.json", b"{}")
    else:
        maintenance._json_write(
            root / "latest.json",
            {
                "schema": recovery.SCHEMA,
                "artifact_id": "../elsewhere",
            },
        )
    assert recovery.recovery_status(settings, verify=True)["successful"] is False


def test_second_destination_failure_preserves_the_previous_complete_pair(fixture, monkeypatch):
    settings, artifact, _ = fixture
    first = recovery.create_recovery(settings, artifact)
    native = recovery._native
    exports = [0]

    def failure(relative, args, env=None):
        if "--export-identity" in args:
            exports[0] += 1
            if exports[0] == 2:
                raise recovery.RecoveryError("native_export_failed")
        return native(relative, args, env)

    monkeypatch.setattr(recovery, "_native", failure)
    with pytest.raises(recovery.RecoveryError, match="native_export_failed"):
        recovery.create_recovery(settings, artifact)
    for root in recovery.recovery_roots(settings).values():
        assert json.loads((root / "latest.json").read_text())["artifact_id"] == first["artifact_id"]


def test_partial_cross_disk_publication_is_not_reported_as_a_complete_pair(fixture):
    settings, artifact, _ = fixture
    first = recovery.create_recovery(settings, artifact)
    recovery.create_recovery(settings, artifact)
    root = recovery.recovery_roots(settings)["internal"]
    maintenance._json_write(
        root / "latest.json",
        {
            "schema": recovery.SCHEMA,
            "artifact_id": first["artifact_id"],
        },
    )
    result = recovery.recovery_status(settings, verify=True)
    assert result["successful"] is False
    assert result["matching_recovery_pair"] is False


def test_unconfigured_authority_cannot_publish_recovery(fixture, monkeypatch):
    settings, artifact, _ = fixture
    native = recovery._native

    def missing(relative, args, env=None):
        if relative == recovery.SECS_BINARY:
            raise recovery.RecoveryError("native_recovery_operation_failed")
        return native(relative, args, env)

    monkeypatch.setattr(recovery, "_native", missing)
    with pytest.raises(recovery.RecoveryError):
        recovery.create_recovery(settings, artifact)
    assert not recovery.recovery_status(settings)["successful"]


@pytest.mark.parametrize("revoked", [True, False])
def test_absent_receiver_admission_requires_explicitly_revoked_authority(fixture, revoked):
    settings, artifact, descriptor = fixture
    name = "support/secrets/secs-magik/devgraph.work.v1/receiver.json"
    (artifact / name).unlink()
    manifest = json.loads((artifact / "manifest.json").read_text())
    del manifest["files"][name]
    maintenance._json_write(artifact / "manifest.json", manifest)
    descriptor["current_authority_valid"] = False
    descriptor["policy"]["rules"][0]["status"] = "revoked" if revoked else "active"
    if not revoked:
        with pytest.raises(recovery.RecoveryError, match="admission_missing"):
            recovery.create_recovery(settings, artifact)
    else:
        result = recovery.create_recovery(settings, artifact)
        for root in recovery.recovery_roots(settings).values():
            value = json.loads((root / result["artifact_id"] / "manifest.json").read_text())
            assert value["receiver_admission"] == "absent_revoked"
        assert recovery.recovery_status(settings, verify=True)["successful"] is True


def test_verify_rechecks_physical_independence_after_creation(fixture, monkeypatch):
    settings, artifact, _ = fixture
    recovery.create_recovery(settings, artifact)
    monkeypatch.setattr(
        recovery,
        "_disk_identity",
        lambda path: {
            "device": 1,
            "physical_disks": ["disk0"],
            "volume_uuid": "moved",
        },
    )
    result = recovery.recovery_status(settings, verify=True)
    assert result["successful"] is False
    assert result["independent_storage_verified"] is False


def test_retention_only_removes_old_verified_pairs_after_new_pair_succeeds(fixture, monkeypatch):
    settings, artifact, _ = fixture
    monkeypatch.setattr(recovery, "KEEP_RECOVERY_PAIRS", 2)
    roots = recovery.recovery_roots(settings)
    first = recovery.create_recovery(settings, artifact)
    second = recovery.create_recovery(settings, artifact)
    unknown = "20260911T120000Z-ffffffffffff"
    for root in roots.values():
        (root / unknown).mkdir(mode=0o700)
    third = recovery.create_recovery(settings, artifact)
    assert third["retention"]["removed_pairs"] == [first["artifact_id"]]
    for root in roots.values():
        assert not (root / first["artifact_id"]).exists()
        assert (root / second["artifact_id"]).is_dir()
        assert (root / third["artifact_id"]).is_dir()
        assert (root / unknown).is_dir()


@pytest.mark.parametrize("fault", ["unknown-field", "extra-directory", "nonfinite-time"])
def test_retention_preserves_future_or_ambiguous_layouts(fixture, monkeypatch, fault):
    settings, artifact, _ = fixture
    monkeypatch.setattr(recovery, "KEEP_RECOVERY_PAIRS", 1)
    first = recovery.create_recovery(settings, artifact)
    roots = recovery.recovery_roots(settings)
    for root in roots.values():
        selected = root / first["artifact_id"]
        if fault == "extra-directory":
            (selected / "future").mkdir(mode=0o700)
        else:
            manifest = json.loads((selected / "manifest.json").read_text())
            if fault == "unknown-field":
                manifest["future"] = {"operator_content": "preserve"}
                maintenance._json_write(selected / "manifest.json", manifest)
            else:
                raw = json.dumps(manifest).replace(str(manifest["created_at"]), "1e999")
                maintenance._write(selected / "manifest.json", raw.encode())
    new = recovery.create_recovery(settings, artifact)
    assert new["retention"]["removed_pairs"] == []
    assert all((root / first["artifact_id"]).exists() for root in roots.values())


@pytest.mark.parametrize("managed_state", ["verified", "missing", "unverified"])
def test_recovery_retry_prefers_newest_verified_database_backup(
    fixture, monkeypatch, managed_state
):
    settings, artifact, _ = fixture
    state = settings.log_root / "maintenance"
    state.mkdir(mode=0o700)
    prior = "20260911T110000Z-000000000009"
    maintenance._json_write(
        state / "last-backup.json",
        {
            "artifact_id": prior,
            "successful": True,
        },
    )
    if managed_state != "missing":
        maintenance._json_write(
            state / "last-managed-backup.json",
            {
                "artifact_id": artifact.name,
                "verified": managed_state == "verified",
            },
        )
    selected = []
    monkeypatch.setattr(recovery, "load_local_config", lambda: settings)
    monkeypatch.setattr(
        recovery, "create_recovery", lambda config, path: selected.append(path.name)
    )
    if managed_state == "unverified":
        with pytest.raises(recovery.RecoveryError, match="verified_backup_required"):
            recovery.run_recovery("create")
        assert not selected
    else:
        recovery.run_recovery("create")
        assert selected == [artifact.name if managed_state == "verified" else prior]
