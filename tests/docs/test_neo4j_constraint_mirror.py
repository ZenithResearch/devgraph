from __future__ import annotations

import re
from pathlib import Path

from devgraph.ops.migrate import load_manifest, render_constraint_mirror

ROOT = Path(__file__).parents[2]


def test_generated_constraint_mirror_has_exact_order_names_set_and_bytes() -> None:
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    mirror = (ROOT / "ontology/neo4j/constraints.cypher").read_text()
    names = re.findall(r"CREATE CONSTRAINT ([a-z0-9_]+) IF NOT EXISTS", mirror)
    expected = [
        migration.name for migration in manifest.migrations
        if migration.kind == "schema_ddl" and migration.name != "work_mutation_guard_id"
    ]
    assert mirror.startswith("// GENERATED; NOT EXECUTED")
    assert mirror == render_constraint_mirror(manifest)
    assert len(names) == 24
    assert names == expected
    assert set(names) == set(expected)
    assert "WorkMutationGuard" not in mirror
    assert manifest.migrations[-2].name == "work_mutation_guard_id"
    assert manifest.migrations[-1].name == "arena_id"
