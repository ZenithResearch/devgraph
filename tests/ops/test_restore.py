from __future__ import annotations

import importlib.util
import inspect
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from devgraph.ops import restore
from devgraph.ops.backup import (
    BACKEND_ID,
    PAYLOAD_MEDIA_TYPE,
    BackupEncryption,
    BackupMetadata,
    create_manifest,
    write_manifest,
)
from devgraph.ops.restore import (
    PostRestoreChecks,
    RestoreCapability,
    RestoreTarget,
    complete_post_restore,
    execute_restore,
    preflight_restore,
)

ROOT = Path(__file__).parents[2]


def capability(**changes) -> RestoreCapability:
    values = {
        "backend_id": BACKEND_ID,
        "backend_version": "1",
        "consistency_modes": ("offline_consistent",),
        "payload_media_types": (PAYLOAD_MEDIA_TYPE,),
        "database_editions": ("community",),
        "runtime_database_version": "5.26.28",
        "payload_count": 1,
        "target_classifications": ("disposable_synthetic",),
    }
    values.update(changes)
    return RestoreCapability(**values)


def target(**changes) -> RestoreTarget:
    values = {
        "logical_id": "restore-fixture-001",
        "physical_identity": "fs-target-fixture-001",
        "classification": "disposable_synthetic",
        "reachable": True,
        "empty": True,
        "available_bytes": 10_000,
    }
    values.update(changes)
    return RestoreTarget(**values)


class RecordingBackend:
    def __init__(
        self,
        *,
        fail: bool = False,
        observed_target: RestoreTarget | None = None,
        observed_capability: RestoreCapability | None = None,
    ) -> None:
        self.fail = fail
        self.target = observed_target or target()
        self.capability = observed_capability or capability()
        self.calls: list[bytes] = []

    def inspect_capability(self) -> RestoreCapability:
        return self.capability

    def inspect_target(self) -> RestoreTarget:
        return self.target

    def load(
        self,
        payloads: tuple[Path, ...],
        expected_target: RestoreTarget,
        required_restore_bytes: int,
    ):
        assert required_restore_bytes >= 1
        assert expected_target.logical_id == self.target.logical_id
        assert expected_target.physical_identity == self.target.physical_identity
        assert expected_target.classification == self.target.classification
        assert expected_target.empty is self.target.empty
        self.calls.append(b"".join(payload.read_bytes() for payload in payloads))
        if self.fail:
            raise RuntimeError("raw backend secret password=do-not-leak")
        return type("Evidence", (), {"exit_code": 0, "operation": "load"})()


class CapacityDropBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.target_inspections = 0

    def inspect_target(self) -> RestoreTarget:
        self.target_inspections += 1
        available = 10_000 if self.target_inspections == 1 else 999
        return replace(self.target, available_bytes=available)


def metadata(*, created_hour: int = 20) -> BackupMetadata:
    return BackupMetadata(
        artifact_id="artifact-fixture-001",
        created_at=datetime(2026, 7, 17, created_hour, 0, tzinfo=timezone.utc),
        source_database_id="source-fixture-001",
        source_storage_identity="fs-source-fixture-001",
        restore_size_bytes=1000,
        neo4j_edition="community",
        neo4j_version="5.26.28",
        migration_current_version=26,
        migration_minimum_version=1,
        migration_maximum_version=26,
        backend_id=BACKEND_ID,
        backend_version="1",
        consistency_mode="offline_consistent",
        payload_media_type=PAYLOAD_MEDIA_TYPE,
        encryption=BackupEncryption(False, "none", None),
        completion_state="complete",
    )


def artifact(root: Path, *, payload: bytes = b"synthetic dump", created_hour: int = 20) -> None:
    root.mkdir()
    (root / "neo4j.dump").write_bytes(payload)
    write_manifest(
        root, create_manifest(root, ("neo4j.dump",), metadata(created_hour=created_hour))
    )


def plan(root: Path, *, target_value=None, capability_value=None):
    return preflight_restore(
        root,
        target_value or target(),
        capability_value or capability(),
        supported_migration_minimum=1,
        supported_migration_maximum=26,
    )


def test_preflight_is_read_only_and_binds_manifest_and_receiver(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)

    result = plan(root)

    assert result.ready is True
    assert result.reason == "restore_preflight_clean"
    assert result.required_restore_bytes == 1000
    assert len(result.manifest_sha256) == 64
    assert result.confirmation_text == (
        f"RESTORE artifact-fixture-001@{result.manifest_sha256} TO restore-fixture-001"
    )
    assert "payload_path" not in result.safe_output()


def test_restore_core_does_not_hardcode_adapter_policy() -> None:
    source = inspect.getsource(restore)

    assert "BACKEND_ID" not in source
    assert "PAYLOAD_MEDIA_TYPE" not in source
    assert '"community"' not in source
    assert '"offline_consistent"' not in source


def test_restore_cli_dry_run_performs_zero_target_mutation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    backend = RecordingBackend()
    module = _restore_cli_module()
    monkeypatch.setattr(module, "Neo4jCommunityOfflineDumpBackend", lambda *args: backend)

    code = module.main(
        [
            "--artifact-directory",
            str(root),
            "--target-volume",
            "devgraph-target-fixture",
            "--dry-run",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["dry_run"] is True
    assert output["ready"] is True
    assert output["reason"] == "restore_preflight_clean"
    assert backend.calls == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"empty": False}, "target_not_empty"),
        ({"reachable": False}, "target_unreachable"),
        ({"classification": "external"}, "target_classification_unsupported"),
        ({"physical_identity": "fs-source-fixture-001"}, "wrong_restore_target"),
        ({"available_bytes": 999}, "insufficient_restore_capacity"),
    ],
)
def test_preflight_rejects_unsafe_targets_before_mutation(
    tmp_path: Path, changes: dict[str, object], reason: str
) -> None:
    root = tmp_path / "artifact"
    artifact(root)

    result = plan(root, target_value=target(**changes))

    assert result.ready is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"backend_id": "wrong-backend"}, "wrong_restore_backend"),
        ({"backend_version": "2"}, "incompatible_backend_version"),
        ({"consistency_modes": ("other",)}, "unsupported_backup_consistency"),
        ({"payload_media_types": ("other/type",)}, "unsupported_backup_payload"),
        ({"database_editions": ("enterprise",)}, "incompatible_database_edition"),
        ({"runtime_database_version": "5.25.1"}, "incompatible_database_version"),
        ({"payload_count": 2}, "unsupported_backup_payload"),
    ],
)
def test_preflight_rejects_incompatible_receiver_capabilities(
    tmp_path: Path, changes: dict[str, object], reason: str
) -> None:
    root = tmp_path / "artifact"
    artifact(root)

    result = plan(root, capability_value=capability(**changes))

    assert result.ready is False
    assert result.reason == reason


def test_preflight_rejects_incompatible_migration_window(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)

    result = preflight_restore(
        root,
        target(),
        capability(),
        supported_migration_minimum=1,
        supported_migration_maximum=22,
    )

    assert result.ready is False
    assert result.reason == "incompatible_migration_version"


def test_execute_requires_digest_bound_confirmation_and_operator_ack(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = RecordingBackend()

    wrong = execute_restore(
        restore_plan, backend, confirmation="wrong", synthetic_confirmation=True
    )
    absent_authority = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=False,
    )

    assert wrong.reason == "restore_confirmation_required"
    assert absent_authority.reason == "synthetic_restore_authority_required"
    assert backend.calls == []


def test_execute_rejects_same_id_artifact_replacement_before_mutation(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = RecordingBackend()
    (root / "neo4j.dump").write_bytes(b"replacement payload")
    (root / "manifest.json").unlink()
    write_manifest(
        root,
        create_manifest(root, ("neo4j.dump",), metadata(created_hour=21)),
    )

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.completed is False
    assert attempt.reason == "artifact_identity_changed"
    assert backend.calls == []


def test_execute_revalidates_physical_target_before_mutation(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = RecordingBackend(observed_target=replace(target(), empty=False))

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.reason == "restore_target_changed"
    assert backend.calls == []


def test_execute_rechecks_capacity_without_requiring_byte_for_byte_stability(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    sufficient = RecordingBackend(observed_target=replace(target(), available_bytes=9000))
    insufficient = RecordingBackend(observed_target=replace(target(), available_bytes=999))

    sufficient_attempt = execute_restore(
        restore_plan,
        sufficient,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )
    insufficient_attempt = execute_restore(
        restore_plan,
        insufficient,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert sufficient_attempt.completed is True
    assert insufficient_attempt.reason == "restore_target_changed"
    assert insufficient.calls == []


def test_execute_rechecks_capacity_after_staging_immediately_before_load(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = CapacityDropBackend()

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.reason == "restore_target_changed"
    assert backend.target_inspections >= 2
    assert backend.calls == []


def test_execute_delivers_full_nested_payload_tuple_with_duplicate_basenames(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "database.dump").write_bytes(b"first payload")
    (root / "b" / "database.dump").write_bytes(b"second payload")
    write_manifest(
        root,
        create_manifest(
            root,
            ("a/database.dump", "b/database.dump"),
            metadata(),
        ),
    )
    two_payloads = capability(payload_count=2)
    restore_plan = plan(root, capability_value=two_payloads)
    backend = RecordingBackend(observed_capability=two_payloads)

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.completed is True
    assert backend.calls == [b"first payloadsecond payload"]


def test_empty_target_restore_uses_owned_staging_and_requires_verification(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = RecordingBackend()

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.completed is True
    assert attempt.ready is False
    assert attempt.reason == "post_restore_verification_required"
    assert attempt.backend_operation == "load"
    assert attempt.backend_exit_code == 0
    assert backend.calls == [b"synthetic dump"]

    verified = complete_post_restore(
        attempt,
        PostRestoreChecks(True, True, True, True, True, True),
    )
    assert verified.ready is True
    assert verified.reason == "restore_verified"


def test_backend_partial_failure_remains_not_ready_and_redacted(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    backend = RecordingBackend(fail=True)

    attempt = execute_restore(
        restore_plan,
        backend,
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )

    assert attempt.completed is False
    assert attempt.ready is False
    assert attempt.reason == "restore_backend_failed"
    assert "secret" not in repr(attempt).lower()
    assert len(backend.calls) == 1


@pytest.mark.parametrize("failed_check", PostRestoreChecks.__dataclass_fields__)
def test_each_post_restore_check_is_required(tmp_path: Path, failed_check: str) -> None:
    root = tmp_path / "artifact"
    artifact(root)
    restore_plan = plan(root)
    attempt = execute_restore(
        restore_plan,
        RecordingBackend(),
        confirmation=restore_plan.confirmation_text,
        synthetic_confirmation=True,
    )
    values = {name: True for name in PostRestoreChecks.__dataclass_fields__}
    values[failed_check] = False

    result = complete_post_restore(attempt, PostRestoreChecks(**values))

    assert result.ready is False
    assert result.reason == f"post_restore_{failed_check}_failed"


def _restore_cli_module():
    path = ROOT / "scripts/devgraph_restore.py"
    spec = importlib.util.spec_from_file_location("devgraph_restore_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restore_cli_has_no_noninteractive_confirmation_flags() -> None:
    module = _restore_cli_module()
    public_options = {
        option
        for action in module.parser()._actions
        for option in action.option_strings
    }

    assert "--confirmation" not in public_options
    assert "--synthetic-confirmation" not in public_options
    assert "--migration-minimum" not in public_options
    assert "--migration-maximum" not in public_options


def test_restore_cli_rejects_noninteractive_execution_before_mutation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "artifact"
    target_root = tmp_path / "target"
    artifact(root)
    target_root.mkdir()
    backend = RecordingBackend()
    module = _restore_cli_module()
    monkeypatch.setattr(module, "Neo4jCommunityOfflineDumpBackend", lambda *args: backend)

    code = module.main(
        [
            "--artifact-directory",
            str(root),
            "--target-volume",
            "devgraph-target-fixture",
        ],
        interactive=False,
        confirmation_reader=lambda prompt: (_ for _ in ()).throw(AssertionError(prompt)),
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["ready"] is False
    assert output["reason"] == "interactive_restore_required"
    assert backend.calls == []


def test_restore_cli_load_only_is_explicit_non_success(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "artifact"
    target_root = tmp_path / "target"
    artifact(root)
    target_root.mkdir()
    backend = RecordingBackend()
    restore_plan = plan(root)
    module = _restore_cli_module()
    monkeypatch.setattr(module, "Neo4jCommunityOfflineDumpBackend", lambda *args: backend)

    code = module.main(
        [
            "--artifact-directory",
            str(root),
            "--target-volume",
            "devgraph-target-fixture",
        ],
        interactive=True,
        confirmation_reader=lambda prompt: restore_plan.confirmation_text,
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 3
    assert output["completed"] is True
    assert output["ready"] is False
    assert output["reason"] == "post_restore_verification_required"
    assert output["operator_action"] == "run_bounded_post_restore_verification"


def test_restore_cli_rejects_receiver_runtime_version_mismatch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "artifact"
    target_root = tmp_path / "target"
    artifact(root)
    target_root.mkdir()
    backend = RecordingBackend(observed_capability=capability(runtime_database_version="5.25.1"))
    module = _restore_cli_module()
    monkeypatch.setattr(module, "Neo4jCommunityOfflineDumpBackend", lambda *args: backend)

    code = module.main(
        [
            "--artifact-directory",
            str(root),
            "--target-volume",
            "devgraph-target-fixture",
            "--dry-run",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["ready"] is False
    assert output["reason"] == "incompatible_database_version"
    assert backend.calls == []
