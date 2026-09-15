#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from devgraph.ops.migrate import (
    ManifestError,
    MigrationStatus,
    apply_migrations,
    load_manifest,
    migration_status,
)
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "migrations" / "manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or inspect Devgraph Neo4j migrations")
    parser.add_argument("command", choices=("status", "apply"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--attempt-id", help="explicit non-secret operator attempt id")
    parser.add_argument(
        "--recover-owner-attempt-id",
        help="explicit expected prior owner id for CAS-safe crash recovery",
    )
    parser.add_argument(
        "--safe-output",
        action="store_true",
        help="emit the stable redacted JSON status contract (always enforced)",
    )
    return parser


def _unavailable_status(manifest) -> MigrationStatus:
    return MigrationStatus.failed("operator_hold_storage_unavailable", manifest)


def _invalid_manifest_output() -> dict[str, object]:
    return {
        "ready": False,
        "reason": "invalid_manifest",
        "manifest_schema_version": 0,
        "minimum_schema_version": 0,
        "maximum_schema_version": 0,
        "current_applied_version": 0,
        "applied": [],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError:
        print(json.dumps(_invalid_manifest_output(), sort_keys=True, separators=(",", ":")))
        return 2
    storage: Neo4jGraphStorage | None = None
    try:
        storage = Neo4jGraphStorage(Neo4jConfig.from_env())
        store = Neo4jMigrationStore(storage)
        if args.command == "apply":
            status = apply_migrations(
                manifest,
                store,
                attempt_id=args.attempt_id or uuid4().hex,
                recovery_owner_attempt_id=args.recover_owner_attempt_id,
            )
        else:
            status = migration_status(manifest, store)
    except StorageUnavailable:
        status = _unavailable_status(manifest)
    finally:
        if storage is not None:
            storage.close()

    print(json.dumps(status.safe_output(), sort_keys=True, separators=(",", ":")))
    return 0 if status.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
