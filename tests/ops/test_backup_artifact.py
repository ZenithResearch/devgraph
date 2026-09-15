from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from devgraph.ops.backup import (
    ARTIFACT_MANIFEST_NAME,
    BACKEND_ID,
    ArtifactError,
    BackupEncryption,
    BackupMetadata,
    create_manifest,
    load_and_verify_artifact,
    stage_verified_payloads,
    write_manifest,
)


def metadata() -> BackupMetadata:
    return BackupMetadata(
        artifact_id="artifact-fixture-001",
        created_at=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
        source_database_id="source-fixture-001",
        source_storage_identity="fs-source-fixture-001",
        restore_size_bytes=1000,
        neo4j_edition="community",
        neo4j_version="5.26.23",
        migration_current_version=23,
        migration_minimum_version=1,
        migration_maximum_version=23,
        backend_id=BACKEND_ID,
        backend_version="1",
        consistency_mode="offline_consistent",
        payload_media_type="application/vnd.neo4j.database-dump",
        encryption=BackupEncryption(
            encrypted=False,
            algorithm_profile="none",
            key_reference=None,
        ),
        completion_state="complete",
    )


def create_artifact(root: Path, payload: bytes = b"synthetic neo4j dump fixture\n"):
    root.mkdir()
    payload_path = root / "neo4j.dump"
    payload_path.write_bytes(payload)
    manifest = create_manifest(root, ("neo4j.dump",), metadata())
    write_manifest(root, manifest)
    return manifest


def test_manifest_is_canonical_deterministic_and_complete(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    manifest = create_artifact(root)

    first = manifest.canonical_bytes()
    second = create_manifest(root, ("neo4j.dump",), metadata()).canonical_bytes()
    decoded = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert decoded["manifest_schema_version"] == 1
    assert decoded["artifact_id"] == "artifact-fixture-001"
    assert decoded["source_database_id"] == "source-fixture-001"
    assert decoded["source_storage_identity"] == "fs-source-fixture-001"
    assert decoded["restore_size_bytes"] == 1000
    assert decoded["neo4j"] == {"edition": "community", "version": "5.26.23"}
    assert decoded["migration"] == {"current": 23, "maximum": 23, "minimum": 1}
    assert decoded["backend"] == {
        "id": BACKEND_ID,
        "version": "1",
        "consistency_mode": "offline_consistent",
    }
    assert decoded["payload"]["size_bytes"] == len(b"synthetic neo4j dump fixture\n")
    assert decoded["files"][0]["relative_path"] == "neo4j.dump"
    assert len(decoded["files"][0]["sha256"]) == 64
    assert decoded["encryption"] == {
        "algorithm_profile": "none",
        "encrypted": False,
        "key_reference": None,
    }
    assert decoded["completion_state"] == "complete"
    assert (root / ARTIFACT_MANIFEST_NAME).read_bytes() == first


def test_artifact_integrity_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    manifest = create_artifact(root)

    verified = load_and_verify_artifact(root)

    assert verified.manifest == manifest
    assert verified.payload_paths == (root / "neo4j.dump",)


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.dump", "/absolute.dump", "nested/../neo4j.dump", "./neo4j.dump", "", "neo4j\\dump"],
)
def test_manifest_rejects_unsafe_relative_paths(tmp_path: Path, relative_path: str) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "neo4j.dump").write_bytes(b"fixture")

    with pytest.raises(ArtifactError, match="invalid_artifact_path"):
        create_manifest(root, (relative_path,), metadata())


def test_manifest_rejects_duplicate_paths(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "neo4j.dump").write_bytes(b"fixture")

    with pytest.raises(ArtifactError, match="duplicate_artifact_path"):
        create_manifest(root, ("neo4j.dump", "neo4j.dump"), metadata())


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "artifact_file_missing"),
        ("extra", "artifact_file_extra"),
        ("corrupt", "artifact_checksum_mismatch"),
        ("size", "artifact_size_mismatch"),
        ("schema", "unsupported_artifact_schema"),
        ("boolean_schema", "unsupported_artifact_schema"),
        ("incomplete", "incomplete_backup_artifact"),
        ("encrypted", "unsupported_backup_encryption"),
        ("malformed", "malformed_backup_manifest"),
    ],
)
def test_integrity_failures_are_classified_before_restore(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    root = tmp_path / "artifact"
    create_artifact(root)
    manifest_path = root / ARTIFACT_MANIFEST_NAME
    manifest_path.chmod(0o600)
    data = json.loads(manifest_path.read_text())
    if mutation == "missing":
        (root / "neo4j.dump").unlink()
    elif mutation == "extra":
        (root / "extra.dump").write_bytes(b"extra")
    elif mutation == "corrupt":
        original = (root / "neo4j.dump").read_bytes()
        (root / "neo4j.dump").write_bytes(b"X" + original[1:])
    elif mutation == "size":
        data["files"][0]["size_bytes"] += 1
        manifest_path.write_text(json.dumps(data))
    elif mutation == "schema":
        data["manifest_schema_version"] = 2
        manifest_path.write_text(json.dumps(data))
    elif mutation == "boolean_schema":
        data["manifest_schema_version"] = True
        manifest_path.write_text(json.dumps(data))
    elif mutation == "incomplete":
        data["completion_state"] = "failed"
        manifest_path.write_text(json.dumps(data))
    elif mutation == "encrypted":
        data["encryption"] = {
            "encrypted": True,
            "algorithm_profile": "unknown",
            "key_reference": "safe-ref",
        }
        manifest_path.write_text(json.dumps(data))
    else:
        manifest_path.write_text("not json")

    with pytest.raises(ArtifactError, match=reason):
        load_and_verify_artifact(root)


def test_metadata_rejects_secret_shaped_key_reference() -> None:
    with pytest.raises(ArtifactError, match="invalid_encryption_metadata"):
        replace(
            metadata(),
            encryption=BackupEncryption(True, "external-v1", "password=raw-secret"),
        )


def test_malformed_created_at_is_normalized_to_artifact_error(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    create_artifact(root)
    manifest_path = root / ARTIFACT_MANIFEST_NAME
    manifest_path.chmod(0o600)
    data = json.loads(manifest_path.read_text())
    data["created_at"] = 7
    manifest_path.write_text(json.dumps(data))

    with pytest.raises(ArtifactError, match="invalid_artifact_created_at"):
        load_and_verify_artifact(root)


@pytest.mark.parametrize("symlink_part", ["ancestor", "root", "manifest"])
def test_artifact_verification_rejects_symlinks(tmp_path: Path, symlink_part: str) -> None:
    real_root = tmp_path / "real-artifact"
    create_artifact(real_root)
    candidate = real_root
    if symlink_part == "ancestor":
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        real_root.rename(real_parent / "artifact")
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        candidate = linked_parent / "artifact"
    elif symlink_part == "root":
        candidate = tmp_path / "artifact-link"
        candidate.symlink_to(real_root, target_is_directory=True)
    else:
        manifest = real_root / ARTIFACT_MANIFEST_NAME
        moved = real_root / "manifest.real"
        manifest.rename(moved)
        manifest.symlink_to(moved.name)

    with pytest.raises(ArtifactError, match="unsafe_artifact_symlink"):
        load_and_verify_artifact(candidate)


def test_staging_preserves_nested_payload_paths_with_duplicate_basenames(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "database.dump").write_bytes(b"first payload")
    (root / "b" / "database.dump").write_bytes(b"second payload")
    write_manifest(
        root,
        create_manifest(root, ("a/database.dump", "b/database.dump"), metadata()),
    )
    verified = load_and_verify_artifact(root)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    staged = stage_verified_payloads(verified, staging)

    assert [path.relative_to(staging).as_posix() for path in staged] == [
        "a/database.dump",
        "b/database.dump",
    ]
    assert [path.read_bytes() for path in staged] == [b"first payload", b"second payload"]


def test_fixed_artifact_error_does_not_retain_filesystem_exception(tmp_path: Path) -> None:
    root = tmp_path / "missing-artifact"
    root.mkdir()

    with pytest.raises(ArtifactError, match="artifact_file_missing") as captured:
        load_and_verify_artifact(root)

    assert captured.value.__cause__ is None


def test_deeply_nested_manifest_is_normalized_without_recursion_traceback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact"
    create_artifact(root)
    manifest = root / ARTIFACT_MANIFEST_NAME
    manifest.chmod(0o600)
    manifest.write_text("[" * 1500 + "]" * 1500)

    with pytest.raises(ArtifactError, match="malformed_backup_manifest") as captured:
        load_and_verify_artifact(root)

    assert captured.value.__cause__ is None
