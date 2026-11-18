"""Core source transfer preserves the packaged runtime compatibility boundary."""
import hashlib
import json
import py_compile
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
    header, *classes = extension["@graph"]
    assert header["owl:imports"] == [{"@id": ownership["canonicalCore"]["ontologyUrl"]}]
    assert all(node["@type"] == "owl:Class" for node in classes)
    assert {node["@id"]: node["subClassOf"] for node in classes} == {
        "devgraph:SessionRef": "zn:ExternalLink",
        "devgraph:SessionLink": "zn:Artifact",
        "devgraph:LocalResourceRegistry": "zn:Collection",
        "devgraph:ServiceInstance": "owl:Thing",
        "devgraph:ZenithRepository": "owl:Thing",
    }
    subprocess.run(["node", "scripts/build_ontology_extension.mjs", "--check"],
                   cwd=ROOT, check=True, capture_output=True)


def test_cli_distinguishes_runtime_release_from_canonical_vocabulary() -> None:
    snapshot = ontology_snapshot()
    assert snapshot["version"] == "0.12.0"
    assert "canonical_vocabulary" not in snapshot
    assert "extension" not in snapshot
    sources = ontology_sources()
    assert sources["adoptedCore"]["version"] == "0.1.0"
    assert sources["adoptedCore"]["repository"].endswith("/zenith-ontology-normalized")
    assert sources["extension"]["version"] == "0.2.0"
    assert sources["extension"]["ontologyId"].endswith("/ontology/devgraph")
    assert sources["runtimeCompatibility"]["version"] == snapshot["version"]
    assert sources["closureVerified"] is True
    assert sources["runtimeAdmission"] is False


@pytest.mark.parametrize("mode", ["missing", "changed", "unlisted", "symlink", "import-pin"])
def test_packaged_closure_rejects_corruption(tmp_path: Path, mode: str) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "src/devgraph/resources", resources)
    target = resources / "ontology-sources/core/v0.1.0/modules/core.jsonld"
    if mode == "missing":
        target.unlink()
    elif mode == "changed":
        target.write_bytes(b"changed")
    elif mode == "unlisted":
        target.with_name("unlisted.json").write_bytes(b"extra")
    elif mode == "symlink":
        target.unlink()
        target.symlink_to("directory.jsonld")
    else:
        index_path = resources / "ontology-source-index.v1.json"
        index = json.loads(index_path.read_text())
        index["adoptedCore"]["manifest"]["sha256"] = "0" * 64
        index_path.write_text(json.dumps(index))
    with pytest.raises((ValueError, OSError)):
        ontology_sources(resources)


def test_legacy_dispositions_cover_removed_classes() -> None:
    legacy = json.loads(
        (ROOT / "ontology/imports/zenith/v0.13.0/ontology.jsonld").read_text()
    )
    normalized = json.loads(
        (ROOT / "ontology/imports/zenith-normalized/v0.1.0/manifest.json").read_text()
    )
    mappings = json.loads(
        (ROOT / "ontology/extension/legacy-term-mappings.json").read_text()
    )["entries"]
    namespace = "https://zenith-research.ca/ontology/terms/"
    removed = {
        node["@id"] for node in legacy["@graph"] if node.get("@type") == "owl:Class"
    } - set(normalized["classes"])
    assert len(mappings) == len(removed) == 22
    by_iri = {entry["legacyIri"]: entry for entry in mappings}
    assert set(by_iri) == {namespace + term.removeprefix("zn:") for term in removed}
    assert by_iri[namespace + "ReviewPacket"]["projection"] == namespace + "Artifact"
    assert by_iri[namespace + "EventReceipt"]["projection"] == namespace + "Receipt"
    assert by_iri[namespace + "CodexSessionRef"]["schemaBase"] == "SessionRef"
    assert by_iri[namespace + "Seat"]["ownerBoundary"] == "organizational-directory"


def test_packaged_closure_accepts_bytecode_written_for_vendored_tool(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "src/devgraph/resources", resources)
    path = resources / "ontology-sources/core/v0.1.0/tools/verify_release.py"
    py_compile.compile(str(path), doraise=True)
    assert ontology_sources(resources)["closureVerified"] is True


def test_valid_hashes_do_not_admit_unresolved_nested_import(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "src/devgraph/resources", resources)
    directory = resources / "ontology-sources/core/v0.1.0"
    work_path = directory / "modules/work.jsonld"
    work = json.loads(work_path.read_text())
    work["@graph"][0]["imports"] = ["urn:unresolved:module"]
    work_bytes = json.dumps(work).encode()
    work_path.write_bytes(work_bytes)

    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    member = next(f for f in manifest["files"] if f["path"] == "modules/work.jsonld")
    member.update(bytes=len(work_bytes), sha256=hashlib.sha256(work_bytes).hexdigest())
    listing = "".join(f"{f['sha256']}  {f['path']}\n" for f in manifest["files"])
    manifest["bundleDigest"] = hashlib.sha256(listing.encode()).hexdigest()
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)

    index_path = resources / "ontology-source-index.v1.json"
    index = json.loads(index_path.read_text())
    index["adoptedCore"]["manifest"].update(
        bytes=len(manifest_bytes), sha256=hashlib.sha256(manifest_bytes).hexdigest()
    )
    index_path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match="unresolved normalized ontology import"):
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
