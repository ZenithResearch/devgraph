from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from devgraph.ops.migrate import ManifestError, load_manifest, render_constraint_mirror

ROOT = Path(__file__).parents[2]
EXPECTED_DDL = [
    "todo_id", "proposal_id", "initiative_id", "project_id", "issue_id", "task_id",
    "requirement_id", "acceptance_criterion_id", "blocker_id", "decision_id", "handoff_id",
    "review_packet_id", "milestone_id", "artifact_id", "external_link_id", "sync_shadow_id",
    "status_set_id", "status_value_id", "status_transition_id", "event_receipt_id",
    "hermes_session_ref_id", "readiness_assessment_id",
]
EXPECTED = [
    *EXPECTED_DDL,
    "canonical_work_object_persistence_v1",
    "event_receipt_idempotency_claim_digest",
    "work_mutation_guard_id", "arena_id",
]


def test_manifest_pins_schema_payloads_transactional_v23_and_claim_constraint_v24() -> None:
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert manifest.schema_version == 1
    assert [item.version for item in manifest.migrations] == list(range(1, 27))
    assert [item.name for item in manifest.migrations] == EXPECTED
    assert [item.kind for item in manifest.migrations[:22]] == ["schema_ddl"] * 22
    assert manifest.migrations[22].kind == "transactional_data"
    assert manifest.migrations[23].kind == "schema_ddl"
    for item in manifest.migrations:
        payload = (ROOT / item.payload_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item.checksum
    for item in (*manifest.migrations[:22], *manifest.migrations[23:]):
        assert item.payload.decode().count(";") == 1
        assert "IF NOT EXISTS" in item.payload.decode()
    transactional = manifest.migrations[22]
    assert transactional.name == "canonical_work_object_persistence_v1"
    assert b"canonical_work_object_persistence_v1" in transactional.payload
    mirror = (ROOT / "ontology/neo4j/constraints.cypher").read_text()
    assert render_constraint_mirror(manifest) == mirror
    assert transactional.payload.decode() not in mirror
    assert manifest.migrations[23].payload.decode() in mirror


def test_manifest_rejects_non_mapping_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]")
    with pytest.raises(ManifestError, match="invalid_manifest"):
        load_manifest(manifest_path)


def test_manifest_rejects_checksum_drift(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    source = ROOT / manifest.migrations[0].payload_path
    changed = tmp_path / source.name
    changed.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ManifestError, match="checksum_mismatch"):
        load_manifest(ROOT / "migrations/manifest.json", payload_override={1: changed})


def _copy_manifest_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "migrations", root / "migrations")
    return root / "migrations/manifest.json"


def test_manifest_rejects_extra_unreferenced_payload(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_tree(tmp_path)
    (manifest_path.parent / "9999_extra.cypher").write_text(
        "CREATE CONSTRAINT extra IF NOT EXISTS FOR (m:Extra) REQUIRE m.id IS UNIQUE;\n"
    )
    with pytest.raises(ManifestError, match="payload_set_mismatch"):
        load_manifest(manifest_path)


def test_manifest_rejects_non_deterministic_payload_filename(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_tree(tmp_path)
    raw = json.loads(manifest_path.read_text())
    source = manifest_path.parents[1] / raw["migrations"][0]["payload_path"]
    renamed = source.with_name("wrong-name.cypher")
    source.rename(renamed)
    raw["migrations"][0]["payload_path"] = "migrations/wrong-name.cypher"
    manifest_path.write_text(json.dumps(raw))
    with pytest.raises(ManifestError, match="invalid_payload_filename"):
        load_manifest(manifest_path)


def test_manifest_rejects_prefix_only_or_wrong_definition_payload(tmp_path: Path) -> None:
    manifest_path = _copy_manifest_tree(tmp_path)
    raw = json.loads(manifest_path.read_text())
    entry = raw["migrations"][0]
    payload = manifest_path.parents[1] / entry["payload_path"]
    changed = (
        b"CREATE CONSTRAINT todo_id IF NOT EXISTS FOR (m:WrongLabel) "
        b"REQUIRE m.wrong_property IS UNIQUE;\n"
    )
    payload.write_bytes(changed)
    entry["checksum"] = hashlib.sha256(changed).hexdigest()
    manifest_path.write_text(json.dumps(raw))
    with pytest.raises(ManifestError, match="payload_definition_mismatch"):
        load_manifest(manifest_path)
