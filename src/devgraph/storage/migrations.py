from __future__ import annotations

from pathlib import Path

from devgraph.storage.base import ConstraintResult

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "migrations" / "manifest.json"


def load_constraint_statements() -> list[str]:
    from devgraph.ops.migrate import load_manifest

    manifest = load_manifest(DEFAULT_MANIFEST)
    return [
        migration.payload.decode("utf-8").rstrip(";\n")
        for migration in manifest.migrations
    ]


def count_idempotent_application(statements: list[str], seen: set[str]) -> ConstraintResult:
    applied = 0
    existing = 0
    for statement in statements:
        if statement in seen:
            existing += 1
        else:
            seen.add(statement)
            applied += 1
    return ConstraintResult(applied_count=applied, existing_count=existing)
