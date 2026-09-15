#!/usr/bin/env python3
"""Build and verify the immutable public Zenith ontology bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY = ROOT / "ontology"
CONFIG_PATH = ONTOLOGY / "publication.json"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    if config.get("schema_version") != 1:
        raise ValueError("unsupported publication schema_version")
    if config.get("release") != f"v{config.get('version')}":
        raise ValueError("release must be v<version>")
    return config


def bundle_digest(files: list[dict]) -> str:
    digest_input = "".join(
        f"{item['sha256']}  {item['path']}\n"
        for item in sorted(files, key=lambda item: item["path"])
    ).encode()
    return sha256(digest_input)


def render_release(destination: Path, config: dict) -> tuple[dict, bytes]:
    destination.mkdir(parents=True, exist_ok=True)
    file_records: list[dict] = []

    for item in config["files"]:
        source = ROOT / item["source"]
        target = destination / item["target"]
        if not source.is_file():
            raise FileNotFoundError(source)
        payload = source.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        file_records.append(
            {
                "path": item["target"],
                "media_type": item["media_type"],
                "bytes": len(payload),
                "sha256": sha256(payload),
                "url": f"{config['canonical_base_url']}/{item['target']}",
            }
        )

    manifest = {
        "schema_version": config["schema_version"],
        "ontology_id": config["ontology_id"],
        "version": config["version"],
        "release": config["release"],
        "published_at": config["published_at"],
        "canonical_base_url": config["canonical_base_url"],
        "discovery_url": config["discovery_url"],
        "authority": config["authority"],
        "compatibility": config["compatibility"],
        "files": sorted(file_records, key=lambda item: item["path"]),
    }
    manifest["bundle_digest_algorithm"] = "sha256-file-list-v1"
    manifest["bundle_digest"] = bundle_digest(manifest["files"])
    manifest_payload = json_bytes(manifest)
    (destination / "manifest.json").write_bytes(manifest_payload)
    return manifest, manifest_payload


def render_discovery(config: dict, manifest: dict, manifest_payload: bytes) -> bytes:
    discovery = {
        "schema_version": 1,
        "ontology_id": config["ontology_id"],
        "canonical_authority": config["authority"],
        "current": {
            "version": config["version"],
            "release": config["release"],
            "manifest_url": f"{config['canonical_base_url']}/manifest.json",
            "manifest_sha256": sha256(manifest_payload),
            "bundle_digest": manifest["bundle_digest"],
            "ontology_url": f"{config['canonical_base_url']}/ontology.jsonld",
            "context_url": f"{config['canonical_base_url']}/context.jsonld",
            "operations_url": f"{config['canonical_base_url']}/operations.json",
            "repository_contract_url": f"{config['canonical_base_url']}/repository-contract.json",
        },
        "supported_versions": config.get("supported_versions", [config["version"]]),
        "compatibility_policy": (
            "Consumers pin version and bundle_digest; additive changes require a new "
            "versioned release."
        ),
    }
    return json_bytes(discovery)


def render_tree(release_destination: Path, discovery_destination: Path) -> None:
    config = load_config()
    manifest, manifest_payload = render_release(release_destination, config)
    discovery_destination.parent.mkdir(parents=True, exist_ok=True)
    discovery_destination.write_bytes(render_discovery(config, manifest, manifest_payload))


def snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def check() -> None:
    config = load_config()
    committed_release = ONTOLOGY / "releases" / config["release"]
    committed_discovery = ONTOLOGY / "discovery.json"
    with tempfile.TemporaryDirectory(prefix="zenith-ontology-check-") as temporary:
        temporary_root = Path(temporary)
        expected_release = temporary_root / config["release"]
        expected_discovery = temporary_root / "discovery.json"
        render_tree(expected_release, expected_discovery)

        if snapshot_tree(committed_release) != snapshot_tree(expected_release):
            raise SystemExit(
                "ontology release bundle is stale; run scripts/build_ontology_bundle.py"
            )
        if (
            not committed_discovery.is_file()
            or committed_discovery.read_bytes() != expected_discovery.read_bytes()
        ):
            raise SystemExit(
                "ontology discovery document is stale; run scripts/build_ontology_bundle.py"
            )


def build() -> None:
    config = load_config()
    release_destination = ONTOLOGY / "releases" / config["release"]
    if release_destination.exists():
        shutil.rmtree(release_destination)
    render_tree(release_destination, ONTOLOGY / "discovery.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail when committed artifacts are stale"
    )
    args = parser.parse_args()
    if args.check:
        check()
    else:
        build()


if __name__ == "__main__":
    main()
