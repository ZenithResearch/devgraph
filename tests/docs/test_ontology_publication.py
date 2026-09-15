from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_canonical_ontology_authority_and_runtime_boundary() -> None:
    publication = read_json(ONTOLOGY / "publication.json")
    ontology = read_json(ONTOLOGY / "ontology.jsonld")

    assert publication["ontology_id"] == "https://zenith-research.ca/ontology"
    assert publication["authority"]["repository"] == "https://github.com/ZenithResearch/devgraph"
    assert publication["canonical_base_url"].endswith(f"/v{publication['version']}")

    graph = ontology["@graph"]
    zenith_repository = next(item for item in graph if item.get("@id") == "zn:ZenithRepository")
    assert zenith_repository["runtimeLabel"] is False

    by_id = {item.get("@id"): item for item in graph}
    assert by_id["zn:Actor"]["runtimeLabel"] is False
    assert by_id["zn:Actor"]["equivalentClass"] == "zn:Entity"
    assert by_id["zn:Entity"]["runtimeLabel"] is False
    for class_id in ("zn:Person", "zn:Agent", "zn:Organization"):
        assert by_id[class_id]["subClassOf"] == "zn:Entity"
        assert by_id[class_id]["runtimeLabel"] is False


def test_operation_catalog_keeps_semantics_separate_from_secs_opcodes() -> None:
    catalog = read_json(ONTOLOGY / "operations.json")
    policy = catalog["transport_policy"]
    assert policy["semantic_names_are_canonical"] is True
    assert policy["secs_opcodes_are_receiver_local"] is True
    assert policy["numeric_opcodes_are_ontology_identifiers"] is False
    assert all("opcode" not in operation for operation in catalog["operations"])
    assert {operation["name"] for operation in catalog["operations"]} >= {
        "get_work_object",
        "create_work_object",
        "update_work_object_content",
        "archive_work_object",
        "transition_work_object_status",
        "devgraph.issue.create.v1",
    }
    exact_operation = next(
        operation
        for operation in catalog["operations"]
        if operation["name"] == "devgraph.issue.create.v1"
    )
    assert exact_operation == {
        "name": "devgraph.issue.create.v1",
        "id": "urn:zenith:devgraph:operation:devgraph.issue.create.v1",
        "mutation": True,
        "required_scopes": ["devgraph.write"],
        "request_schema_id": (
            "urn:zenith:devgraph:schema:devgraph-issue-create-request:v1"
        ),
        "response_schema_id": "urn:zenith:devgraph:schema:mutation-result:v1",
    }
    assert all(
        selector not in exact_operation
        for selector in ("opcode", "transport", "route", "handler")
    )


def test_release_manifest_verifies_every_payload_and_bundle_digest() -> None:
    publication = read_json(ONTOLOGY / "publication.json")
    release = ONTOLOGY / "releases" / publication["release"]
    manifest = read_json(release / "manifest.json")

    digest_lines: list[tuple[str, str]] = []
    for item in manifest["files"]:
        payload = (release / item["path"]).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        assert digest == item["sha256"]
        assert len(payload) == item["bytes"]
        digest_lines.append((item["path"], f"{digest}  {item['path']}\n"))

    expected_bundle_digest = hashlib.sha256(
        "".join(line for _, line in sorted(digest_lines)).encode()
    ).hexdigest()
    assert expected_bundle_digest == manifest["bundle_digest"]


def test_discovery_pins_manifest_and_release_digest() -> None:
    publication = read_json(ONTOLOGY / "publication.json")
    release = ONTOLOGY / "releases" / publication["release"]
    manifest_payload = (release / "manifest.json").read_bytes()
    manifest = json.loads(manifest_payload)
    discovery = read_json(ONTOLOGY / "discovery.json")

    assert discovery["current"]["version"] == manifest["version"]
    assert discovery["current"]["bundle_digest"] == manifest["bundle_digest"]
    assert discovery["current"]["manifest_sha256"] == hashlib.sha256(manifest_payload).hexdigest()
    assert discovery["supported_versions"] == ["0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0"]


def test_historical_release_manifests_and_payloads_remain_byte_pinned() -> None:
    manifest_hashes = {
        "v0.5.0": "3a66e495399475507d880455343e52e6b5cd527dfee459d00f912f85528ca9c2",
        "v0.1.0": "89e97eb269e508d7e47a8eef037283ecb00956ddb4e3edbf1ebc7cdcd6b344f9",
        "v0.2.0": "7f51df66c55c855b68a97a518ba22e43d4b12b0500c8277118fcdf93ba7bb87b",
        "v0.3.0": "0096e68bb0956612e162f77b90e39d42529ac3954fff2e39073315b5b59ae2b2",
        "v0.4.0": "598790ad585474376170504f6d8c5e435c486c2fa6b93d3507abf0f098627f01",
    }
    for release_name, expected_manifest_hash in manifest_hashes.items():
        release = ONTOLOGY / "releases" / release_name
        manifest_payload = (release / "manifest.json").read_bytes()
        assert hashlib.sha256(manifest_payload).hexdigest() == expected_manifest_hash
        manifest = json.loads(manifest_payload)
        for item in manifest["files"]:
            payload = (release / item["path"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            assert len(payload) == item["bytes"]


def test_exact_receipt_profile_is_additive_and_distinct_from_legacy_digest() -> None:
    classes = (ONTOLOGY / "classes.md").read_text()
    assert "Exact `devgraph.issue.create.v1` receipt profile" in classes
    assert "`request_digest_sha256`" in classes
    assert "`idempotency_key_digest_sha256`" in classes
    assert "not the legacy `idempotency_key_digest`" in classes
    assert "No uniqueness constraint" in classes


def test_committed_publication_artifacts_are_deterministic() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_ontology_bundle.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_current_repository_contract_pins_current_ontology_version() -> None:
    publication = read_json(ONTOLOGY / "publication.json")
    release = ONTOLOGY / "releases" / publication["release"]

    assert read_json(ONTOLOGY / "repository-contract.json")[
        "ontology_version"
    ] == publication["version"]
    assert read_json(release / "repository-contract.json")[
        "ontology_version"
    ] == publication["version"]
