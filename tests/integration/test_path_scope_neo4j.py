"""Opt-in live storage parity against an empty, disposable Neo4j test database.

This does not use local.json, any installed graph, member grants or credentials.
The runner must provision a fresh fixture and explicitly set the test URI.
"""

import os
from pathlib import Path

import pytest
from tests.auth.test_resource_paths import ids, move, read
from tests.auth.test_write_scope import create, hierarchy

from devgraph.ops.migrate import apply_migrations, load_manifest
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore


@pytest.mark.skipif(
    not os.environ.get("DEVGRAPH_PATH_TEST_NEO4J_URI"),
    reason="requires a dedicated empty resource-path fixture database",
)
def test_live_path_counts_future_children_reparenting_and_corrupt_ancestry():
    storage = Neo4jGraphStorage(Neo4jConfig(os.environ["DEVGRAPH_PATH_TEST_NEO4J_URI"], "", ""))
    assert storage.query("Initiative") == [] and storage.query("Task") == []
    manifest = load_manifest(Path(__file__).parents[2] / "migrations/manifest.json")
    assert apply_migrations(manifest, Neo4jMigrationStore(storage), attempt_id="path-fixture").ready
    hierarchy(storage)
    selected = read("Arena/a", "Initiative/root-a", "Project/project-a")
    assert ids(storage, selected) == ({"Project/project-a", "Task/child"}, 2)
    create(storage, "Task", "future")
    storage.create_edge("Project", "project-a", "HAS_CHILD", "Task", "future")
    assert ids(storage, selected)[1] == 3
    with storage.work_mutation_transaction():
        move(storage)
    assert ids(storage, selected) == ({"Project/project-a", "Task/future"}, 2)
    with storage.work_mutation_transaction():
        move(storage, source="project-b", target="project-a")
    assert ids(storage, selected)[1] == 3
    # Historical generic records were stored without the newer JSON codec marker.
    # Their decoded canonical Arena still confers the same path, with no migration.
    arena = storage.get_node("Arena", "a")
    storage._run_graph(
        "MATCH (a:Arena {id: 'a'}) SET a = $properties",
        properties={"id": arena.id, "archived": arena.archived, **arena.properties},
    )
    assert ids(storage, selected)[1] == 3
    # An encoded-looking kind with no matching codec marker must not confer scope.
    storage._run_graph(
        "MATCH (a:Arena {id: 'a'}) SET a.kind = $kind",
        kind=storage._generic_properties({"kind": "Arena"})["kind"],
    )
    assert ids(storage, selected) == (set(), 0)
    storage._run_graph("MATCH (a:Arena {id: 'a'}) SET a.kind = 'Arena'")
    storage.create_edge("Initiative", "root-b", "HAS_CHILD", "Project", "project-a")
    assert ids(storage, selected) == (set(), 0)
