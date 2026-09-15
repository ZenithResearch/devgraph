from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_neo4j_migrations import EXPECTED_DIGEST, IMAGE, _docker, _wait_for_bolt

from devgraph.api import ApiServices, create_app
from devgraph.model.work import Task
from devgraph.ops.migrate import apply_migrations, load_manifest
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

WORKER_IMAGE_DIGEST = "sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf"
WORKER_IMAGE = f"python:3.12-slim@{WORKER_IMAGE_DIGEST}"
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


def _probe(storage, manifest, store) -> tuple[int, dict[str, object]]:
    response = TestClient(create_app(_services(storage, manifest, store))).get("/ready")
    return response.status_code, response.json()


def _exercise(uri: str) -> dict[str, Any]:
    storage = Neo4jGraphStorage(Neo4jConfig(uri, "", ""))
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    store = Neo4jMigrationStore(storage)
    evidence: dict[str, Any] = {}
    try:
        live = TestClient(create_app(_services(storage, manifest, store))).get("/live")
        evidence["live_before_migrations"] = [live.status_code, live.json()]
        evidence["before_migrations"] = _probe(storage, manifest, store)

        applied = apply_migrations(manifest, store, attempt_id="readiness-live")
        evidence["apply_reason"] = applied.reason
        evidence["clean"] = _probe(storage, manifest, store)

        store._run("MATCH (m:DevgraphMigration {version: 23}) DELETE m")
        evidence["unapplied_v23"] = _probe(storage, manifest, store)
        assert apply_migrations(manifest, store, attempt_id="restore-v23").ready is True

        store._run("MATCH (m:DevgraphMigration) WHERE m.version >= 1 DELETE m")
        evidence["below_minimum"] = _probe(storage, manifest, store)
        assert apply_migrations(manifest, store, attempt_id="restore-all").ready is True

        v23 = store.inspect_journal()[-1]
        store._run(
            "MATCH (m:DevgraphMigration {version: 23}) "
            "SET m.state = 'pending', m.completed_at = null"
        )
        evidence["dirty_v23"] = _probe(storage, manifest, store)
        store._run(
            "MATCH (m:DevgraphMigration {version: 23}) SET m = $data",
            data=asdict(v23),
        )

        assert store.acquire_owner("readiness-lock") is True
        evidence["locked"] = _probe(storage, manifest, store)
        assert store.release_owner("readiness-lock") is True

        first_checksum = manifest.migrations[0].checksum
        store._run(
            "MATCH (m:DevgraphMigration {version: 1}) SET m.checksum = $checksum",
            checksum="0" * 64,
        )
        evidence["checksum_mismatch"] = _probe(storage, manifest, store)
        store._run(
            "MATCH (m:DevgraphMigration {version: 1}) SET m.checksum = $checksum",
            checksum=first_checksum,
        )

        extra = asdict(store.inspect_journal()[-1])
        extra["version"] = 24
        store._run("CREATE (extra:DevgraphMigration) SET extra = $data", data=extra)
        evidence["above_maximum"] = _probe(storage, manifest, store)
        store._run("MATCH (m:DevgraphMigration {version: 24}) DELETE m")

        storage._run_graph(
            "CREATE (:Task {id: $node_id, title: 'Missing kind', archived: false})",
            node_id="task-missing-kind",
        )
        evidence["missing_kind"] = _probe(storage, manifest, store)
        storage._run_graph("MATCH (n:Task {id: $node_id}) DELETE n", node_id="task-missing-kind")

        task = Task(id="task-archive-mismatch", title="Archive mismatch")
        storage.create_node(task.kind, task.id, task.to_node_properties())
        storage._run_graph(
            "MATCH (n:Task {id: $node_id}) SET n.archived = true",
            node_id=task.id,
        )
        evidence["archive_mismatch"] = _probe(storage, manifest, store)
        storage._run_graph("MATCH (n:Task {id: $node_id}) DELETE n", node_id=task.id)

        canonical = Task(id="task-corruption-template", title="Corruption template")
        storage._run_graph(
            "CREATE (n:Task {archived: false, corruption_case: 'missing-id'}) "
            "SET n += $properties",
            properties=canonical.to_node_properties(),
        )
        evidence["missing_id"] = _probe(storage, manifest, store)
        storage._run_graph("MATCH (n:Task {corruption_case: 'missing-id'}) DELETE n")

        storage._run_graph(
            "CREATE (n:Task {id: $node_id, corruption_case: 'missing-archived'}) "
            "SET n += $properties",
            node_id="task-missing-archived",
            properties=canonical.to_node_properties(),
        )
        evidence["missing_archived"] = _probe(storage, manifest, store)
        storage._run_graph(
            "MATCH (n:Task {corruption_case: 'missing-archived'}) DELETE n"
        )

        storage._run_graph(
            "CREATE (n:Task:Project {id: $node_id, archived: false}) "
            "SET n += $properties",
            node_id="task-multiple-labels",
            properties=canonical.to_node_properties(),
        )
        evidence["multiple_labels"] = _probe(storage, manifest, store)
        storage._run_graph(
            "MATCH (n:Task {id: $node_id}) DELETE n", node_id="task-multiple-labels"
        )

        storage.close()
        evidence["storage_unavailable"] = _probe(storage, manifest, store)
        live_after_close = TestClient(create_app(_services(storage, manifest, store))).get("/live")
        evidence["live_after_storage_close"] = [
            live_after_close.status_code,
            live_after_close.json(),
        ]
        return evidence
    finally:
        storage.close()


def _run_worker(network: str) -> dict[str, Any]:
    command = (
        "python -m pip install --quiet "
        "'neo4j==6.2.0' 'fastapi==0.128.8' 'httpx==0.28.1' 'pytest==8.3.5' && "
        "PYTHONPATH=/workspace/src python "
        "/workspace/tests/integration/test_neo4j_readiness.py "
        "--worker bolt://neo4j-readiness:7687"
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
        timeout=240,
    )
    return json.loads(output.splitlines()[-1])


def run_live_readiness_proof() -> dict[str, Any]:
    suffix = uuid4().hex[:12]
    network = f"devgraph-readiness-{suffix}"
    name = f"devgraph-readiness-{suffix}"
    container = ""
    result: dict[str, Any] = {}
    _docker("network", "create", network)
    try:
        container = _docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "neo4j-readiness",
            "--env",
            "NEO4J_AUTH=none",
            IMAGE,
        )
        _wait_for_bolt(container)
        published_ports = _docker(
            "inspect", container, "--format", "{{json .NetworkSettings.Ports}}"
        )
        evidence = _run_worker(network)
        digest = _docker("image", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}")
        result = {
            **evidence,
            "image_digest": digest.rsplit("@", 1)[-1],
            "published_ports": published_ports,
        }
    finally:
        rm_container = (
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if container
            else None
        )
        rm_network = subprocess.run(
            ["docker", "network", "rm", network],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_absent = not container or subprocess.run(
            ["docker", "inspect", container],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode != 0
        network_absent = subprocess.run(
            ["docker", "network", "inspect", network],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode != 0
        result["cleanup"] = bool(
            (rm_container is None or rm_container.returncode == 0)
            and rm_network.returncode == 0
            and container_absent
            and network_absent
        )
    return result


@pytest.mark.skipif(
    os.environ.get("DEVGRAPH_TEST_NEO4J") != "1", reason="explicit disposable Neo4j gate"
)
def test_pinned_neo4j_migration_aware_readiness() -> None:
    evidence = run_live_readiness_proof()

    assert evidence["image_digest"] == EXPECTED_DIGEST
    assert all(binding is None for binding in json.loads(str(evidence["published_ports"])).values())
    assert evidence["live_before_migrations"] == [200, {"live": True}]
    assert evidence["apply_reason"] == "clean"
    assert evidence["clean"][0] == 200
    assert evidence["clean"][1]["reason"] == "clean"
    expected_failures = {
        "before_migrations": "operator_hold_bootstrap_inspection",
        "unapplied_v23": "unapplied_migrations",
        "below_minimum": "unapplied_migrations",
        "dirty_v23": "operator_hold_journal_state",
        "locked": "migration_lock_busy",
        "checksum_mismatch": "checksum_mismatch",
        "above_maximum": "operator_hold_journal_version_out_of_range",
        "missing_kind": "canonical_persistence_invalid",
        "archive_mismatch": "canonical_persistence_invalid",
        "missing_id": "canonical_persistence_invalid",
        "missing_archived": "canonical_persistence_invalid",
        "multiple_labels": "canonical_persistence_invalid",
        "storage_unavailable": "canonical_storage_unavailable",
    }
    for key, reason in expected_failures.items():
        assert evidence[key][0] == 503
        assert evidence[key][1]["ready"] is False
        assert evidence[key][1]["reason"] == reason
    assert evidence["live_after_storage_close"] == [200, {"live": True}]
    assert evidence["cleanup"] is True


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    print(json.dumps(_exercise(sys.argv[2]), sort_keys=True))
