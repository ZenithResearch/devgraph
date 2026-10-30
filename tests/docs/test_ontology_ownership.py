"""Core source transfer preserves the packaged runtime compatibility boundary."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from devgraph.cli import ontology_snapshot
from devgraph.ontology_sources import ontology_sources

ROOT = Path(__file__).resolve().parents[2]


def test_release_primitives_and_conformance_fixtures_match_vendor_pin() -> None:
    vendor = json.loads((ROOT / "scripts/lib/vendor.json").read_text())
    assert vendor["repository"] == "https://github.com/ZenithResearch/zenith-ontology"
    for name, digest in vendor["files"].items():
        assert hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() == digest


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
    assert "canonical_vocabulary" not in snapshot
    assert "extension" not in snapshot
    sources = ontology_sources()
    assert sources["adoptedCore"]["version"] == "0.13.0"
    assert sources["adoptedCore"]["repository"].endswith("/zenith-ontology")
    assert sources["extension"]["ontologyId"].endswith("/ontology/devgraph")
    assert sources["runtimeCompatibility"]["version"] == snapshot["version"]
    assert sources["closureVerified"] is True
    assert sources["runtimeAdmission"] is False


@pytest.mark.parametrize("mode", ["missing", "changed", "unlisted", "symlink", "import-pin"])
def test_packaged_closure_rejects_corruption(tmp_path: Path, mode: str) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "src/devgraph/resources", resources)
    target = resources / "ontology-sources/core/v0.13.0/context.jsonld"
    if mode == "missing":
        target.unlink()
    elif mode == "changed":
        target.write_bytes(b"changed")
    elif mode == "unlisted":
        target.with_name("unlisted.json").write_bytes(b"extra")
    elif mode == "symlink":
        target.unlink()
        target.symlink_to("ontology.jsonld")
    else:
        index_path = resources / "ontology-source-index.v1.json"
        index = json.loads(index_path.read_text())
        index["adoptedCore"]["manifest"]["sha256"] = "0" * 64
        index_path.write_text(json.dumps(index))
    with pytest.raises((ValueError, OSError)):
        ontology_sources(resources)


def test_source_ownership_drift_fails_before_release_or_resource_mutation(tmp_path: Path) -> None:
    for directory in ("ontology", "scripts", "src/devgraph/resources"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    ownership_path = tmp_path / "ontology/ownership.json"
    ownership = json.loads(ownership_path.read_text())
    ownership["canonicalCore"]["version"] = "99.0.0"
    ownership_path.write_text(json.dumps(ownership))
    roots = [tmp_path / "ontology/extension/releases", tmp_path / "src/devgraph/resources"]
    before = {str(p): p.read_bytes() for r in roots for p in r.rglob("*") if p.is_file()}
    for args in ([], ["--check"]):
        result = subprocess.run(["node", "scripts/build_ontology_extension.mjs", *args],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode != 0
        assert "Ownership/import drift" in result.stderr
        assert before == {str(p): p.read_bytes()
                          for r in roots for p in r.rglob("*") if p.is_file()}


def test_legacy_authoring_cannot_regenerate_shared_core() -> None:
    result = subprocess.run(["python3", "scripts/build_ontology_bundle.py"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "ZenithResearch/zenith-ontology" in result.stderr
