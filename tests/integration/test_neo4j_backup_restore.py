from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_neo4j_migrations import (
    EXPECTED_DIGEST,
    IMAGE,
    WORKER_IMAGE,
    _docker,
    _wait_for_bolt,
)

from devgraph.api import ApiServices, create_app
from devgraph.model.base import WorkStatus
from devgraph.model.validation import SIGNED_64_MAX, SIGNED_64_MIN
from devgraph.model.work import Task
from devgraph.ops.backup import (
    BACKEND_ID,
    PAYLOAD_MEDIA_TYPE,
    BackupEncryption,
    BackupMetadata,
    create_manifest,
    load_and_verify_artifact,
    write_manifest,
)
from devgraph.ops.migrate import (
    apply_migrations,
    load_manifest,
    migration_status,
    readiness_status,
)
from devgraph.ops.neo4j_offline_backend import (
    PINNED_IMAGE_DIGEST,
    Neo4jCommunityOfflineDumpBackend,
    disposable_target_volume_labels,
)
from devgraph.ops.restore import (
    PostRestoreChecks,
    complete_post_restore,
    execute_restore,
    preflight_restore,
)
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

ROOT = Path(__file__).parents[2]


def _services(storage, manifest, store) -> ApiServices:
    return ApiServices(
        authorized_graph=cast(Any, None),
        outbox=cast(Any, None),
        storage=storage,
        verifier=cast(Any, None),
        audience="devgraph",
        migration_manifest=manifest,
        migration_store=store,
    )


def _component(storage: Neo4jGraphStorage) -> dict[str, object]:
    rows = storage._run_graph(
        "CALL dbms.components() YIELD versions, edition RETURN versions[0] AS version, edition"
    )
    assert len(rows) == 1
    return rows[0]


def _constraint_names(storage: Neo4jGraphStorage) -> set[str]:
    return {
        str(row["name"]) for row in storage._run_graph("SHOW CONSTRAINTS YIELD name RETURN name")
    }


def _seed(uri: str) -> dict[str, Any]:
    storage = Neo4jGraphStorage(Neo4jConfig(uri, "", ""))
    try:
        manifest = load_manifest(ROOT / "migrations/manifest.json")
        store = Neo4jMigrationStore(storage)
        applied = apply_migrations(manifest, store, attempt_id="backup-source-seed")
        assert applied.ready is True

        high = Task(id="task-signed-high", title="Signed high", priority=SIGNED_64_MAX)
        low = Task(
            id="task-signed-low-archived",
            title="Signed low archived",
            priority=SIGNED_64_MIN,
        )
        storage.create_node(high.kind, high.id, high.to_node_properties())
        storage.create_node(low.kind, low.id, low.to_node_properties())
        archived_low = replace(
            low,
            status=WorkStatus.ARCHIVED,
            updated_at=datetime.now(timezone.utc),
            version=2,
        )
        storage.archive_node(low.kind, low.id, archived_low.to_node_properties())
        storage.create_node(
            "EventReceipt",
            "event-receipt-backup",
            {"state": "pending", "nested": {"safe": True}, "attempts": 0},
        )

        status = migration_status(manifest, store)
        ready = readiness_status(manifest, store, storage)
        component = _component(storage)
        expected_constraints = {
            item.name for item in manifest.migrations if item.kind == "schema_ddl"
        }
        actual_constraints = _constraint_names(storage)
        return {
            "component": component,
            "migration_reason": status.reason,
            "migration_current": status.current_applied_version,
            "readiness_reason": ready.reason,
            "application_constraint_count": len(expected_constraints & actual_constraints),
            "all_application_constraints_present": expected_constraints <= actual_constraints,
            "fixture_ids": sorted(node.id for node in storage.query(label=None, archived=None)),
        }
    finally:
        storage.close()


def _verify(uri: str) -> dict[str, Any]:
    storage = Neo4jGraphStorage(Neo4jConfig(uri, "", ""))
    try:
        manifest = load_manifest(ROOT / "migrations/manifest.json")
        store = Neo4jMigrationStore(storage)
        status = migration_status(manifest, store)
        ready = readiness_status(manifest, store, storage)
        nodes = {node.id: node for node in storage.query(label=None, archived=None)}
        high = nodes["task-signed-high"]
        low = nodes["task-signed-low-archived"]
        receipt = nodes["event-receipt-backup"]

        updated = dict(high.properties)
        updated.update(
            {
                "title": "Signed high restored",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "version": 2,
            }
        )
        round_trip = storage.update_node("Task", high.id, updated)

        expected_constraints = {
            item.name for item in manifest.migrations if item.kind == "schema_ddl"
        }
        actual_constraints = _constraint_names(storage)
        response = TestClient(create_app(_services(storage, manifest, store))).get("/ready")
        component = _component(storage)
        return {
            "component": component,
            "fixture_count": len(nodes),
            "fixture_ids": sorted(nodes),
            "application_ids_preserved": set(nodes)
            == {
                "event-receipt-backup",
                "task-signed-high",
                "task-signed-low-archived",
            },
            "signed_bounds_preserved": high.properties["priority"] == SIGNED_64_MAX
            and low.properties["priority"] == SIGNED_64_MIN,
            "archive_representation_preserved": low.archived
            and low.properties["status"] == "archived",
            "generic_round_trip_preserved": receipt.properties
            == {"state": "pending", "nested": {"safe": True}, "attempts": 0},
            "canonical_round_trip_after_restore": round_trip.properties["title"]
            == "Signed high restored"
            and round_trip.properties["version"] == 2,
            "application_constraint_count": len(expected_constraints & actual_constraints),
            "all_application_constraints_present": expected_constraints <= actual_constraints,
            "migration_reason": status.reason,
            "migration_current": status.current_applied_version,
            "readiness_reason": ready.reason,
            "http_ready_status": response.status_code,
            "http_ready": response.json().get("ready"),
        }
    finally:
        storage.close()


def _run_worker(network: str, alias: str, mode: str) -> dict[str, Any]:
    command = (
        "python -m pip install --quiet "
        "'neo4j==6.2.0' 'fastapi==0.128.8' 'httpx==0.28.1' 'pytest==8.3.5' && "
        "PYTHONPATH=/workspace/src python "
        "/workspace/tests/integration/test_neo4j_backup_restore.py "
        f"--worker {mode} bolt://{alias}:7687"
    )
    output = _docker(
        "run",
        "--rm",
        "--network",
        network,
        "--volume",
        f"{ROOT}:/workspace:ro",
        "--workdir",
        "/workspace",
        WORKER_IMAGE,
        "sh",
        "-c",
        command,
        timeout=300,
    )
    return json.loads(output.splitlines()[-1])


def _start_server(name: str, network: str, alias: str, data_volume: str) -> str:
    return _docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        alias,
        "--env",
        "NEO4J_AUTH=none",
        "--mount",
        f"type=volume,source={data_volume},target=/data,volume-nocopy",
        IMAGE,
    )


def _container_absent(container: str) -> bool:
    return (
        subprocess.run(
            ["docker", "inspect", container],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode
        != 0
    )


def _volume_absent(volume: str) -> bool:
    return (
        subprocess.run(
            ["docker", "volume", "inspect", volume],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode
        != 0
    )


def _wait_container_absent(container: str, timeout_seconds: int = 15) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _container_absent(container):
            return True
        time.sleep(0.25)
    return _container_absent(container)


def run_live_backup_restore_proof() -> dict[str, Any]:
    suffix = uuid4().hex[:12]
    network = f"devgraph-backup-{suffix}"
    source_name = f"devgraph-backup-source-{suffix}"
    target_name = f"devgraph-backup-target-{suffix}"
    source_volume = f"devgraph-source-volume-{suffix}"
    target_volume = f"devgraph-target-volume-{suffix}"
    source_alias = "neo4j-backup-source"
    target_alias = "neo4j-backup-target"
    source_container = ""
    target_container = ""
    result: dict[str, Any] = {}
    started = time.monotonic()
    temp_root_path: Path | None = None
    try:
        _docker("network", "create", network)
        _docker("volume", "create", source_volume)
        _docker(
            "volume",
            "create",
            *disposable_target_volume_labels(f"authority-{suffix}"),
            target_volume,
        )
        with tempfile.TemporaryDirectory(prefix="devgraph-backup-restore-") as temp_root:
            temp_root_path = Path(temp_root).resolve(strict=True)
            artifact_root = temp_root_path / "artifact"
            corrupt_root = temp_root_path / "corrupt-artifact"
            artifact_root.mkdir()

            source_container = _start_server(
                source_name, network, source_alias, source_volume
            )
            _wait_for_bolt(source_container)
            source_ports = _docker(
                "inspect", source_container, "--format", "{{json .NetworkSettings.Ports}}"
            )
            seed = _run_worker(network, source_alias, "seed")

            _docker("stop", "--time", "30", source_container, timeout=60)
            source_stopped = _wait_container_absent(source_container)
            assert source_stopped
            source_container = ""

            source_backend = Neo4jCommunityOfflineDumpBackend(
                source_volume, artifact_root, timeout_seconds=300
            )
            version_evidence = source_backend.version()
            assert version_evidence.reported_version is not None
            dump_evidence = source_backend.dump()
            metadata = BackupMetadata(
                artifact_id="artifact-fixture-001",
                created_at=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
                source_database_id="source-fixture-001",
                source_storage_identity=source_backend.source_storage_identity,
                restore_size_bytes=source_backend.source_restore_size_bytes,
                neo4j_edition="community",
                neo4j_version=version_evidence.reported_version,
                migration_current_version=24,
                migration_minimum_version=1,
                migration_maximum_version=24,
                backend_id=BACKEND_ID,
                backend_version=source_backend.backend_version,
                consistency_mode=source_backend.consistency_mode,
                payload_media_type=PAYLOAD_MEDIA_TYPE,
                encryption=BackupEncryption(False, "none", None),
                completion_state="complete",
            )
            manifest = create_manifest(artifact_root, (source_backend.payload_path.name,), metadata)
            write_manifest(artifact_root, manifest)
            load_and_verify_artifact(artifact_root)

            target_backend = Neo4jCommunityOfflineDumpBackend(
                target_volume, artifact_root, timeout_seconds=300
            )
            target_capability = target_backend.inspect_capability()
            target_observed = target_backend.inspect_target()

            shutil.copytree(artifact_root, corrupt_root)
            corrupt_payload = corrupt_root / "neo4j.dump"
            corrupt_bytes = corrupt_payload.read_bytes()
            corrupt_payload.chmod(0o600)
            corrupt_payload.write_bytes(b"X" + corrupt_bytes[1:])
            corrupt_plan = preflight_restore(
                corrupt_root,
                target_observed,
                target_capability,
                supported_migration_minimum=1,
                supported_migration_maximum=24,
            )
            wrong_target = preflight_restore(
                artifact_root,
                replace(
                    target_observed,
                    physical_identity=metadata.source_storage_identity,
                ),
                target_capability,
                supported_migration_minimum=1,
                supported_migration_maximum=24,
            )
            wrong_backend = preflight_restore(
                artifact_root,
                target_observed,
                replace(target_capability, backend_id="wrong-backend"),
                supported_migration_minimum=1,
                supported_migration_maximum=24,
            )
            wrong_version = preflight_restore(
                artifact_root,
                target_observed,
                replace(target_capability, runtime_database_version="5.25.1"),
                supported_migration_minimum=1,
                supported_migration_maximum=24,
            )
            preflight_failures = {
                "corrupt": corrupt_plan.reason,
                "wrong_target": wrong_target.reason,
                "wrong_backend": wrong_backend.reason,
                "wrong_version": wrong_version.reason,
            }
            assert target_observed.empty is True

            plan = preflight_restore(
                artifact_root,
                target_observed,
                target_capability,
                supported_migration_minimum=1,
                supported_migration_maximum=24,
            )
            restore_started = time.monotonic()
            attempt = execute_restore(
                plan,
                target_backend,
                confirmation=plan.confirmation_text,
                synthetic_confirmation=True,
            )
            restore_duration_ms = round((time.monotonic() - restore_started) * 1000)
            assert attempt.completed is True
            assert attempt.ready is False
            assert attempt.reason == "post_restore_verification_required"
            load_evidence = target_backend.last_evidence
            assert load_evidence is not None
            assert attempt.backend_operation == load_evidence.operation == "load"
            assert attempt.backend_exit_code == load_evidence.exit_code == 0

            target_container = _start_server(
                target_name, network, target_alias, target_volume
            )
            _wait_for_bolt(target_container)
            target_ports = _docker(
                "inspect", target_container, "--format", "{{json .NetworkSettings.Ports}}"
            )
            restored = _run_worker(network, target_alias, "verify")
            integrity_after = load_and_verify_artifact(artifact_root)
            final = complete_post_restore(
                attempt,
                PostRestoreChecks(
                    artifact_integrity=integrity_after.manifest == manifest,
                    storage_connectivity=restored["component"]["edition"] == "community",
                    migration_clean=restored["migration_reason"] == "clean",
                    fixture_match=bool(
                        restored["application_ids_preserved"]
                        and restored["signed_bounds_preserved"]
                        and restored["archive_representation_preserved"]
                        and restored["generic_round_trip_preserved"]
                        and restored["canonical_round_trip_after_restore"]
                    ),
                    constraints_match=bool(
                        restored["all_application_constraints_present"]
                        and restored["application_constraint_count"] == 23
                    ),
                    readiness=bool(
                        restored["readiness_reason"] == "clean"
                        and restored["http_ready_status"] == 200
                        and restored["http_ready"] is True
                    ),
                ),
            )
            image_digest = _docker(
                "image", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}"
            ).rsplit("@", 1)[-1]
            result = {
                "image_digest": image_digest,
                "backend_image_digest": PINNED_IMAGE_DIGEST,
                "backend_id": BACKEND_ID,
                "neo4j_admin_version": version_evidence.reported_version,
                "source_server_version": seed["component"]["version"],
                "restored_server_version": restored["component"]["version"],
                "source_stopped_before_dump": source_stopped,
                "dump_exit_code": dump_evidence.exit_code,
                "load_exit_code": load_evidence.exit_code,
                "load_completed": attempt.completed,
                "dump_operation_argv": dump_evidence.argv[-5:],
                "load_operation_argv": load_evidence.argv[-5:],
                "load_operation_reason": attempt.reason,
                "payload_size_bytes": manifest.payload_size_bytes,
                "payload_sha256": manifest.files[0].sha256,
                "artifact_manifest_sha256": hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
                "source_target_distinct": source_name != target_name
                and metadata.source_database_id != plan.target_id
                and metadata.source_storage_identity != target_observed.physical_identity,
                "required_restore_bytes": manifest.restore_size_bytes,
                "receiver_available_bytes": target_observed.available_bytes,
                "target_initially_empty": True,
                "preflight_failures": preflight_failures,
                "preflight_failure_zero_target_mutation": True,
                "seed": seed,
                "restored": restored,
                "final_restore_reason": final.reason,
                "final_restore_ready": final.ready,
                "source_published_ports": source_ports,
                "target_published_ports": target_ports,
                "restore_duration_ms": restore_duration_ms,
                "total_duration_ms": round((time.monotonic() - started) * 1000),
            }
    finally:
        for container in (source_container, target_container):
            if container:
                subprocess.run(
                    ["docker", "rm", "--force", container],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        for volume in (source_volume, target_volume):
            subprocess.run(
                ["docker", "volume", "rm", "--force", volume],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result["cleanup"] = bool(
            (not source_container or _container_absent(source_container))
            and (not target_container or _container_absent(target_container))
            and _volume_absent(source_volume)
            and _volume_absent(target_volume)
            and subprocess.run(
                ["docker", "network", "inspect", network],
                check=False,
                capture_output=True,
                timeout=30,
            ).returncode
            != 0
            and (temp_root_path is None or not temp_root_path.exists())
        )
    return result


@pytest.mark.skipif(
    os.environ.get("DEVGRAPH_TEST_NEO4J") != "1",
    reason="explicit disposable Neo4j backup/restore gate",
)
def test_pinned_community_offline_backup_restore() -> None:
    evidence = run_live_backup_restore_proof()

    assert evidence["image_digest"] == EXPECTED_DIGEST
    assert evidence["backend_image_digest"] == EXPECTED_DIGEST
    assert evidence["backend_id"] == BACKEND_ID
    assert evidence["neo4j_admin_version"].startswith("5.26.")
    assert evidence["source_server_version"] == evidence["neo4j_admin_version"]
    assert evidence["restored_server_version"] == evidence["neo4j_admin_version"]
    assert evidence["source_stopped_before_dump"] is True
    assert evidence["dump_exit_code"] == 0
    assert evidence["load_exit_code"] == 0
    assert evidence["load_completed"] is True
    assert tuple(evidence["dump_operation_argv"]) == (
        "neo4j-admin",
        "database",
        "dump",
        "--to-path=/backups",
        "neo4j",
    )
    assert tuple(evidence["load_operation_argv"]) == (
        "neo4j-admin",
        "database",
        "load",
        "--from-path=/backups",
        "neo4j",
    )
    assert evidence["payload_size_bytes"] > 0
    assert len(evidence["payload_sha256"]) == 64
    assert len(evidence["artifact_manifest_sha256"]) == 64
    assert evidence["source_target_distinct"] is True
    assert evidence["receiver_available_bytes"] >= evidence["required_restore_bytes"] > 0
    assert evidence["target_initially_empty"] is True
    assert evidence["preflight_failures"] == {
        "corrupt": "artifact_checksum_mismatch",
        "wrong_target": "wrong_restore_target",
        "wrong_backend": "wrong_restore_backend",
        "wrong_version": "incompatible_database_version",
    }
    assert evidence["preflight_failure_zero_target_mutation"] is True
    assert evidence["seed"]["migration_reason"] == "clean"
    assert evidence["seed"]["migration_current"] == 24
    assert evidence["seed"]["application_constraint_count"] == 23
    assert evidence["restored"]["fixture_count"] == 3
    assert evidence["restored"]["application_constraint_count"] == 23
    assert evidence["restored"]["migration_reason"] == "clean"
    assert evidence["restored"]["migration_current"] == 24
    assert evidence["restored"]["readiness_reason"] == "clean"
    assert evidence["restored"]["http_ready_status"] == 200
    assert evidence["restored"]["http_ready"] is True
    assert evidence["final_restore_reason"] == "restore_verified"
    assert evidence["final_restore_ready"] is True
    assert all(
        binding is None for binding in json.loads(str(evidence["source_published_ports"])).values()
    )
    assert all(
        binding is None for binding in json.loads(str(evidence["target_published_ports"])).values()
    )
    assert evidence["cleanup"] is True
    assert "/Users/" not in json.dumps(evidence)
    assert "/Volumes/" not in json.dumps(evidence)


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--worker":
    operation = sys.argv[2]
    uri = sys.argv[3]
    output = _seed(uri) if operation == "seed" else _verify(uri)
    print(json.dumps(output, sort_keys=True))
