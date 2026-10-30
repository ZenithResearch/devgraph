"""Offline, read-only inspection of the exact packaged ontology closure."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
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
        if (manifest["ontology_id"] != descriptor["ontologyId"]
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
                actual.add(member.relative_to(directory).as_posix())
        if actual != expected:
            raise ValueError("packaged ontology inventory mismatch")
        digest = "".join(f"{f['sha256']}  {f['path']}\n"
                         for f in sorted(records, key=lambda f: f["path"]))
        if _sha(digest.encode()) != manifest["bundle_digest"]:
            raise ValueError("packaged ontology bundle mismatch")
        releases[pin["url"]] = manifest
        result[role] = {**descriptor, "manifestPath": str(manifest_path),
                        "ontologyPath": str(directory / "ontology.jsonld")}
    for role in ("adoptedCore", "extension"):
        descriptor = result[role]
        manifest = releases[descriptor["manifest"]["url"]]
        imported_urls = []
        for dependency in manifest["imports"]:
            target = releases.get(dependency["manifest"]["url"])
            candidates = [result[k] for k in ("adoptedCore", "extension")
                          if result[k]["manifest"] == dependency["manifest"]]
            if (target is None or len(candidates) != 1
                    or target["ontology_id"] != dependency["ontologyId"]
                    or target["version"] != dependency["version"]):
                raise ValueError("missing exact packaged ontology import")
            imported_urls.append(dependency["ontologyUrl"])
        document = json.loads(Path(descriptor["ontologyPath"]).read_bytes())
        metadata = next(n for n in document["@graph"] if n["@id"] == descriptor["ontologyId"])
        if sorted(n["@id"] for n in metadata.get("owl:imports", [])) != sorted(imported_urls):
            raise ValueError("packaged OWL imports differ from pinned closure")
    result["closureVerified"] = True
    return result
