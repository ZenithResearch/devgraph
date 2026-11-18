"""Offline, read-only inspection of the exact packaged ontology closure."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from importlib.util import source_from_cache
from pathlib import Path, PurePosixPath
from typing import Any


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _path(root: Path, member: str) -> Path:
    name = PurePosixPath(member)
    if (not member or name.is_absolute() or "\\" in member
            or any(p in {"", ".", ".."} for p in member.split("/"))):
        raise ValueError("unsafe ontology resource path")
    current = root
    for part in name.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlinked ontology resource")
    return current


def _verify_normalized_closure(
    directory: Path, manifest: dict[str, Any], descriptor: dict[str, Any], members: set[str]
) -> None:
    documents: dict[str, list[str]] = {}
    paths: set[str] = set()
    for item in manifest["ontologyDocuments"]:
        name, iri = item["path"], item["versionIri"]
        if name not in members or name in paths or iri in documents:
            raise ValueError("invalid normalized ontology document inventory")
        paths.add(name)
        document = json.loads(_path(directory, name).read_bytes())
        headers = [n for n in document["@graph"] if n.get("@type") == "owl:Ontology"]
        if len(headers) != 1:
            raise ValueError("invalid normalized ontology header")
        header = headers[0]
        if header["@id"] != item["ontologyId"] or header["versionIRI"] != iri:
            raise ValueError("normalized ontology identity mismatch")
        documents[iri] = header.get("imports", [])
        if name == "ontology.jsonld" and (
            header["@id"] != descriptor["ontologyId"] or iri != descriptor["ontologyUrl"]
        ):
            raise ValueError("normalized root ontology identity mismatch")
    if paths != {name for name in members if name.endswith(".jsonld")}:
        raise ValueError("normalized ontology document inventory mismatch")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(iri: str) -> None:
        if iri in visiting:
            raise ValueError("cyclic normalized ontology import")
        if iri not in documents:
            raise ValueError("unresolved normalized ontology import")
        if iri in visited:
            return
        visiting.add(iri)
        for dependency in documents[iri]:
            visit(dependency)
        visiting.remove(iri)
        visited.add(iri)

    visit(descriptor["ontologyUrl"])
    if visited != documents.keys():
        raise ValueError("unreachable normalized ontology document")


def ontology_sources(resources: Path | None = None) -> dict[str, Any]:
    """Verify package-relative manifests and return the versioned capability view."""
    root = resources or Path(str(files("devgraph").joinpath("resources")))
    index_path = root / "ontology-source-index.v1.json"
    index = json.loads(index_path.read_bytes())
    if (index.get("schemaVersion") != "devgraph.ontology-sources.v1"
            or index.get("pathBase") != "package-resources"
            or index.get("runtimeAdmission") is not False):
        raise ValueError("unsupported ontology source index")
    releases: dict[str, dict[str, Any]] = {}
    result = dict(index)
    result["sourceIndexPath"] = str(index_path)
    for role in ("adoptedCore", "extension", "runtimeCompatibility"):
        descriptor = index[role]
        directory = _path(root, descriptor["resourcePath"])
        manifest_path = _path(directory, "manifest.json")
        raw = manifest_path.read_bytes()
        pin = descriptor["manifest"]
        if _sha(raw) != pin["sha256"] or len(raw) != pin["bytes"]:
            raise ValueError("packaged ontology manifest pin mismatch")
        manifest = json.loads(raw)
        normalized = manifest.get("schemaVersion") == "normalized-ontology-release.v1"
        identity_key = "ontologyId" if normalized else "ontology_id"
        digest_key = "bundleDigest" if normalized else "bundle_digest"
        if (manifest[identity_key] != descriptor["ontologyId"]
                or manifest["version"] != descriptor["version"]):
            raise ValueError("packaged ontology identity mismatch")
        expected = {"manifest.json"}
        records = manifest["files"]
        for record in records:
            name = record["path"]
            if name in expected:
                raise ValueError("duplicate ontology resource")
            expected.add(name)
            member = _path(directory, name)
            data = member.read_bytes()
            if _sha(data) != record["sha256"] or len(data) != record["bytes"]:
                raise ValueError("packaged ontology resource pin mismatch")
        actual = set()
        for member in directory.rglob("*"):
            if member.is_symlink() or not (member.is_file() or member.is_dir()):
                raise ValueError("unsafe packaged ontology member")
            if member.is_file():
                if normalized and member.suffix == ".pyc" and member.parent.name == "__pycache__":
                    source = Path(source_from_cache(member))
                    if source.relative_to(directory).as_posix() in expected:
                        continue
                actual.add(member.relative_to(directory).as_posix())
        if actual != expected:
            raise ValueError("packaged ontology inventory mismatch")
        digest = "".join(f"{f['sha256']}  {f['path']}\n"
                         for f in sorted(records, key=lambda f: f["path"]))
        if _sha(digest.encode()) != manifest[digest_key]:
            raise ValueError("packaged ontology bundle mismatch")
        if normalized:
            if manifest["versionIri"] != descriptor["ontologyUrl"]:
                raise ValueError("normalized root ontology version mismatch")
            _verify_normalized_closure(directory, manifest, descriptor, expected)
        releases[pin["url"]] = manifest
        result[role] = {**descriptor, "manifestPath": str(manifest_path),
                        "ontologyPath": str(directory / "ontology.jsonld")}
    for role in ("adoptedCore", "extension"):
        descriptor = result[role]
        manifest = releases[descriptor["manifest"]["url"]]
        if manifest.get("schemaVersion") == "normalized-ontology-release.v1":
            continue
        imported_urls = []
        for dependency in manifest["imports"]:
            target = releases.get(dependency["manifest"]["url"])
            candidates = [result[k] for k in ("adoptedCore", "extension")
                          if result[k]["manifest"] == dependency["manifest"]]
            if (target is None or len(candidates) != 1
                    or candidates[0]["ontologyId"] != dependency["ontologyId"]
                    or candidates[0]["ontologyUrl"] != dependency["ontologyUrl"]
                    or target["version"] != dependency["version"]):
                raise ValueError("missing exact packaged ontology import")
            imported_urls.append(dependency["ontologyUrl"])
        document = json.loads(Path(descriptor["ontologyPath"]).read_bytes())
        metadata = next(n for n in document["@graph"] if n["@id"] == descriptor["ontologyId"])
        if sorted(n["@id"] for n in metadata.get("owl:imports", [])) != sorted(imported_urls):
            raise ValueError("packaged OWL imports differ from pinned closure")
    result["closureVerified"] = True
    return result
