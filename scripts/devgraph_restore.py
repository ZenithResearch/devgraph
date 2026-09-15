from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from devgraph.ops.migrate import load_manifest
from devgraph.ops.neo4j_offline_backend import BackendError, Neo4jCommunityOfflineDumpBackend
from devgraph.ops.restore import execute_restore, preflight_restore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Preflight or execute a disposable database restore"
    )
    result.add_argument("--artifact-directory", type=Path, required=True)
    result.add_argument("--target-volume", required=True)
    result.add_argument("--dry-run", action="store_true")
    return result


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def main(
    argv: list[str] | None = None,
    *,
    interactive: bool | None = None,
    confirmation_reader: Callable[[str], str] = input,
) -> int:
    args = parser().parse_args(argv)
    try:
        migration_manifest = load_manifest(
            Path(__file__).resolve().parents[1] / "migrations" / "manifest.json"
        )
        if not migration_manifest.migrations:
            raise ValueError("empty_migration_manifest")
        migration_minimum = migration_manifest.migrations[0].version
        migration_maximum = migration_manifest.migrations[-1].version
        backend = Neo4jCommunityOfflineDumpBackend(
            args.target_volume,
            args.artifact_directory,
        )
        capability = backend.inspect_capability()
        target = backend.inspect_target()
        plan = preflight_restore(
            args.artifact_directory,
            target,
            capability,
            supported_migration_minimum=migration_minimum,
            supported_migration_maximum=migration_maximum,
        )
        if args.dry_run or not plan.ready:
            output = plan.safe_output()
            output["dry_run"] = bool(args.dry_run)
            _print(output)
            return 0 if plan.ready else 2
        if interactive is None:
            interactive = sys.stdin.isatty()
        if interactive is not True:
            _print(
                {
                    "completed": False,
                    "ready": False,
                    "reason": "interactive_restore_required",
                }
            )
            return 2
        confirmation = confirmation_reader(f"Type {plan.confirmation_text} to continue: ")
        attempt = execute_restore(
            plan,
            backend,
            confirmation=confirmation,
            synthetic_confirmation=True,
        )
        output = attempt.safe_output()
        if attempt.reason == "post_restore_verification_required":
            output["operator_action"] = "run_bounded_post_restore_verification"
        _print(output)
        if attempt.ready:
            return 0
        if attempt.completed and attempt.reason == "post_restore_verification_required":
            return 3
        return 2
    except (BackendError, EOFError, OSError, ValueError):
        _print({"completed": False, "ready": False, "reason": "restore_failed"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
