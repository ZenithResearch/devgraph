"""Core source transfer preserves the packaged runtime compatibility boundary."""
import json
import subprocess
from pathlib import Path

from devgraph.cli import ontology_snapshot

ROOT = Path(__file__).resolve().parents[2]


def test_extension_imports_exact_core_without_redeclaring_shared_classes() -> None:
    extension = json.loads((ROOT / "ontology/extension/ontology.jsonld").read_text())
    ownership = json.loads((ROOT / "ontology/ownership.json").read_text())
    assert len(extension["@graph"]) == 1
    assert extension["@graph"][0]["owl:imports"] == [
        {"@id": ownership["canonicalCore"]["ontologyUrl"]}
    ]
    subprocess.run(["node", "scripts/build_ontology_extension.mjs", "--check"],
                   cwd=ROOT, check=True, capture_output=True)


def test_cli_distinguishes_runtime_release_from_canonical_vocabulary() -> None:
    snapshot = ontology_snapshot()
    assert snapshot["version"] == "0.12.0"
    assert snapshot["canonical_vocabulary"]["version"] == "0.13.0"
    assert snapshot["canonical_vocabulary"]["repository"].endswith("/zenith-ontology")
    assert snapshot["extension"]["ontologyId"].endswith("/ontology/devgraph")


def test_legacy_authoring_cannot_regenerate_shared_core() -> None:
    result = subprocess.run(["python3", "scripts/build_ontology_bundle.py"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "ZenithResearch/zenith-ontology" in result.stderr
