from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from devgraph.ops.backup import (
    BACKEND_ID,
    PAYLOAD_MEDIA_TYPE,
    ArtifactError,
    BackupEncryption,
    BackupMetadata,
    create_manifest,
    load_and_verify_artifact,
    write_manifest,
)
from devgraph.ops.neo4j_offline_backend import BackendError, Neo4jCommunityOfflineDumpBackend


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create a disposable offline Neo4j backup artifact"
    )
    result.add_argument("--data-volume", required=True)
    result.add_argument("--artifact-directory", type=Path, required=True)
    result.add_argument("--artifact-id", required=True)
    result.add_argument("--source-database-id", required=True)
    result.add_argument("--created-at", required=True)
    result.add_argument("--neo4j-version", required=True)
    result.add_argument("--migration-current", type=int, required=True)
    result.add_argument("--migration-minimum", type=int, required=True)
    result.add_argument("--migration-maximum", type=int, required=True)
    result.add_argument("--source-stopped", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    return result


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.source_stopped:
        _print({"completed": False, "reason": "source_stopped_assertion_required"})
        return 2
    try:
        backend = Neo4jCommunityOfflineDumpBackend(args.data_volume, args.artifact_directory)
        if any(args.artifact_directory.iterdir()):
            raise ValueError("backup_target_not_empty")
        if args.dry_run:
            _print(
                {
                    "completed": False,
                    "dry_run": True,
                    "reason": "backup_plan_clean",
                    "artifact_id": args.artifact_id,
                    "backend_id": BACKEND_ID,
                }
            )
            return 0
        version_evidence = backend.version()
        if version_evidence.reported_version != args.neo4j_version:
            raise ValueError("incompatible_neo4j_version")
        dump_evidence = backend.dump()
        metadata = BackupMetadata(
            artifact_id=args.artifact_id,
            created_at=datetime.fromisoformat(args.created_at.replace("Z", "+00:00")),
            source_database_id=args.source_database_id,
            source_storage_identity=backend.source_storage_identity,
            restore_size_bytes=backend.source_restore_size_bytes,
            neo4j_edition="community",
            neo4j_version=args.neo4j_version,
            migration_current_version=args.migration_current,
            migration_minimum_version=args.migration_minimum,
            migration_maximum_version=args.migration_maximum,
            backend_id=BACKEND_ID,
            backend_version=backend.backend_version,
            consistency_mode=backend.consistency_mode,
            payload_media_type=PAYLOAD_MEDIA_TYPE,
            encryption=BackupEncryption(False, "none", None),
            completion_state="complete",
        )
        manifest = create_manifest(
            args.artifact_directory,
            (backend.payload_path.name,),
            metadata,
        )
        write_manifest(args.artifact_directory, manifest)
        verified = load_and_verify_artifact(args.artifact_directory)
        if verified.manifest != manifest:
            raise ArtifactError("backup_verification_failed")
        _print(
            {
                "completed": True,
                "reason": "backup_complete",
                "artifact_id": manifest.artifact_id,
                "backend_id": manifest.backend_id,
                "image_digest": dump_evidence.image_digest,
                "version_exit_code": version_evidence.exit_code,
                "dump_exit_code": dump_evidence.exit_code,
                "payload_size_bytes": manifest.payload_size_bytes,
                "payload_sha256": manifest.files[0].sha256,
            }
        )
        return 0
    except (ArtifactError, BackendError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", "backup_failed")
        _print({"completed": False, "reason": reason})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
