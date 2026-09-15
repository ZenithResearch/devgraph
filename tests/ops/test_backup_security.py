from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

from devgraph.ops import backup, neo4j_offline_backend, restore

ROOT = Path(__file__).parents[2]
PRODUCTION_PATHS = (
    ROOT / "src/devgraph/ops/backup.py",
    ROOT / "src/devgraph/ops/restore.py",
    ROOT / "src/devgraph/ops/neo4j_offline_backend.py",
    ROOT / "scripts/devgraph_backup.py",
    ROOT / "scripts/devgraph_restore.py",
)


def production_source() -> str:
    return "\n".join(path.read_text() for path in PRODUCTION_PATHS)


def test_backup_restore_never_use_shell_or_arbitrary_command_templates() -> None:
    source = production_source()

    assert "shell=True" not in source
    assert "os.system" not in source
    assert "shell=True" not in inspect.getsource(neo4j_offline_backend)
    assert "command_template" not in source
    assert "--overwrite-destination" not in source


def test_backup_restore_surface_has_no_credential_arguments_or_environment_dump() -> None:
    source = production_source().lower()

    assert "--password" not in source
    assert "neo4j_password" not in source
    assert "os.environ" not in source
    assert "environment=" not in source


def test_only_offline_community_backend_is_exposed() -> None:
    source = "\n".join(path.read_text() for path in PRODUCTION_PATHS[2:])

    assert neo4j_offline_backend.BACKEND_ID == "neo4j-community-5.26-offline-dump-v1"
    assert "online_backup" not in source.lower()
    assert "aura" not in source.lower()
    assert "logical_export" not in source.lower()


def test_concrete_backend_uses_named_volumes_not_host_bind_paths() -> None:
    source = inspect.getsource(neo4j_offline_backend.Neo4jCommunityOfflineDumpBackend)

    assert "data_directory" not in source
    assert ':/data' not in source
    assert ':/backups' not in source
    assert "--volumes-from" in source
    assert "type=volume,source=" in source
    assert "volume-nocopy" in source


def test_scripts_are_bounded_cli_entrypoints_not_import_side_effects() -> None:
    for path in PRODUCTION_PATHS[-2:]:
        source = path.read_text()
        assert 'if __name__ == "__main__":' in source
        assert "main()" in source


def test_safe_outputs_never_include_payload_paths() -> None:
    assert "payload_path" not in inspect.getsource(restore.RestorePreflight.safe_output)
    assert "payload_path" not in inspect.getsource(restore.RestoreAttempt.safe_output)
    assert "raw" not in inspect.getsource(backup.BackupManifest.canonical_bytes).lower()


def test_intermediate_symlink_swap_cannot_substitute_verified_payload(
    tmp_path: Path, monkeypatch
) -> None:
    artifact = tmp_path / "artifact"
    payload_directory = artifact / "nested"
    payload_directory.mkdir(parents=True)
    payload = payload_directory / "database.dump"
    original = b"receiver-verified-payload"
    payload.write_bytes(original)
    external = tmp_path / "external"
    external.mkdir()
    (external / payload.name).write_bytes(b"attacker-substitute-data")
    moved_directory = artifact / "nested.pinned"
    original_open = backup.os.open
    swapped = False

    def swap_intermediate() -> None:
        nonlocal swapped
        if swapped:
            return
        payload_directory.rename(moved_directory)
        payload_directory.symlink_to(external, target_is_directory=True)
        swapped = True

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and Path(path) == payload:
            swap_intermediate()
            return original_open(path, flags, mode)
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and path == payload_directory.name:
            swap_intermediate()
        return descriptor

    monkeypatch.setattr(backup.os, "open", racing_open)

    size, digest, captured = backup._stream_regular_file(payload, capture_limit=1024)

    assert swapped is True
    assert size == len(original)
    assert digest == hashlib.sha256(original).hexdigest()
    assert captured == original


def test_backup_cli_redacts_filesystem_errors(tmp_path: Path) -> None:
    source = tmp_path / "source"
    artifact = tmp_path / "artifact"
    source.mkdir()
    artifact.mkdir(mode=0o000)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/devgraph_backup.py"),
                "--data-volume",
                "devgraph-source-fixture",
                "--artifact-directory",
                str(artifact),
                "--artifact-id",
                "redacted-artifact-001",
                "--source-database-id",
                "redacted-source-001",
                "--created-at",
                "2026-07-20T17:00:00Z",
                "--neo4j-version",
                "5.26.28",
                "--migration-current",
                "23",
                "--migration-minimum",
                "1",
                "--migration-maximum",
                "23",
                "--source-stopped",
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        artifact.chmod(0o700)

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "completed": False,
        "reason": "backup_failed",
    }
    assert completed.stderr == ""
    assert str(tmp_path) not in completed.stdout + completed.stderr
