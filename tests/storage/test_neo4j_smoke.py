from __future__ import annotations

import os

import pytest

from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, neo4j_available


@pytest.mark.skipif(
    os.environ.get("DEVGRAPH_TEST_NEO4J") != "1",
    reason="set DEVGRAPH_TEST_NEO4J=1 to run Neo4j smoke test",
)
def test_neo4j_smoke_health_and_constraints_are_idempotent():
    if not neo4j_available():
        pytest.skip("neo4j Python driver is not installed")

    config = Neo4jConfig.from_env()
    storage = Neo4jGraphStorage(config)

    assert storage.health().live is True
    first = storage.apply_constraints_from_file()
    second = storage.apply_constraints_from_file()
    assert first.applied_count >= 0
    assert second.existing_count >= first.applied_count
