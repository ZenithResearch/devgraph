from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from devgraph.ops.migrate import MigrationJournal, load_manifest
from devgraph.storage.base import MigrationStore
from devgraph.storage.neo4j import Neo4jMigrationStore

ROOT = Path(__file__).parents[2]


def test_migration_store_is_dedicated_protocol_with_canonical_implementation() -> None:
    assert MigrationStore.__name__ == "MigrationStore"
    assert Neo4jMigrationStore.__name__ == "Neo4jMigrationStore"


def test_driver_access_is_confined_to_canonical_storage_implementation() -> None:
    forbidden = {"GraphDatabase", "_driver", "_session"}
    for path in [ROOT / "src/devgraph/ops/migrate.py", ROOT / "scripts/devgraph_migrate.py"]:
        tree = ast.parse(path.read_text())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not forbidden.intersection(names | attrs)


def test_runtime_never_reads_constraint_mirror() -> None:
    migrations_source = (ROOT / "src/devgraph/storage/migrations.py").read_text()
    tree = ast.parse(migrations_source)
    string_parts = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "ontology" not in string_parts
    assert "constraints.cypher" not in string_parts
    assert "DEFAULT_CONSTRAINT_FILE" not in migrations_source

    neo4j_source = (ROOT / "src/devgraph/storage/neo4j.py").read_text()
    assert "load_constraint_statements" not in neo4j_source
    assert "migrations/manifest.json" not in neo4j_source


def test_migration_protocol_is_typed_and_storage_does_not_import_ops() -> None:
    base_source = (ROOT / "src/devgraph/storage/base.py").read_text()
    protocol = base_source.split("class MigrationStore", 1)[1].split("class GraphStorage", 1)[0]
    assert "-> object" not in protocol
    assert ": object" not in protocol
    neo4j_source = (ROOT / "src/devgraph/storage/neo4j.py").read_text()
    assert "from devgraph.ops" not in neo4j_source


def test_arbitrary_constraint_file_and_broad_graph_protocol_are_absent() -> None:
    migrations_source = (ROOT / "src/devgraph/storage/migrations.py").read_text()
    assert "path: Path | None" not in migrations_source
    base_source = (ROOT / "src/devgraph/storage/base.py").read_text()
    graph_protocol = base_source.split("class GraphStorage", 1)[1]
    assert "apply_constraints" not in graph_protocol
    neo4j_source = (ROOT / "src/devgraph/storage/neo4j.py").read_text()
    graph_storage = neo4j_source.split("class Neo4jGraphStorage", 1)[1].split(
        "class Neo4jMigrationStore", 1
    )[0]
    assert "def apply_constraints" not in graph_storage


def test_release_owner_requires_valid_owned_posture_and_non_null_terminal_states() -> None:
    store = object.__new__(Neo4jMigrationStore)
    captured = {}

    def run(query: str, **parameters):
        captured["query"] = query
        return [{"released": 0}]

    store._run = run
    assert store.release_owner("attempt") is False
    query = captured["query"]
    assert "m.state = 'owned'" in query
    assert "m.runner_schema_version = 1" in query
    assert "j.state IS NULL" in query
    assert "NOT j.state IN" in query


def test_recover_owner_cas_matches_full_journal_identity() -> None:
    store = object.__new__(Neo4jMigrationStore)
    captured = {}

    def run(query: str, **parameters):
        captured["query"] = query
        return [{"recovered": 0}]

    store._run = run
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[0]
    journal = MigrationJournal.started(migration, "old-owner")
    assert store.recover_owner("old-owner", "new-owner", journal) is False
    query = captured["query"]
    for field in (
        "schema_object_name",
        "schema_object_type",
        "started_at",
        "completed_at",
        "runner_schema_version",
    ):
        assert f"j.{field}" in query


def test_journal_compare_and_set_checks_full_record_identity() -> None:
    store = object.__new__(Neo4jMigrationStore)
    captured = {}

    def run(query: str, **parameters):
        captured["query"] = query
        return [{"changed": 0}]

    store._run = run
    migration = load_manifest(ROOT / "migrations/manifest.json").migrations[0]
    before = MigrationJournal.started(migration, "attempt-a")
    after = replace(before, state="ddl_observed")

    assert store.compare_and_set_journal(before, after, "attempt-a") is False
    query = captured["query"]
    assert "m.owner_attempt_id = $before_owner_attempt_id" in query
    assert "m.name = $before_name" in query
    assert "m.checksum = $before_checksum" in query
    assert "m.schema_object_name = $before_schema_object_name" in query
    assert "m.schema_object_type = $before_schema_object_type" in query
    assert "m.schema_object_definition = $before_schema_object_definition" in query
    assert "m.started_at = $before_started_at" in query
    assert "m.completed_at = $before_completed_at" in query
    assert "m.runner_schema_version = $before_runner_schema_version" in query
    assert "version: 0, owner_attempt_id: $attempt" in query
    assert "version: $before_version" in query


def test_journal_compare_and_set_rejects_cross_version_without_query() -> None:
    store = object.__new__(Neo4jMigrationStore)
    calls = []
    store._run = lambda *args, **kwargs: calls.append((args, kwargs))
    migrations = load_manifest(ROOT / "migrations/manifest.json").migrations
    before = MigrationJournal.started(migrations[0], "attempt-a")
    after = MigrationJournal.started(migrations[1], "attempt-a")

    assert store.compare_and_set_journal(before, after, "attempt-a") is False
    assert calls == []
