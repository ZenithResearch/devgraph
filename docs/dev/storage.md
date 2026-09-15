# Storage implementation

`GraphStorage` defines node/edge operations, deterministic queries,
transactions, canonical-persistence inspection, and health. The memory adapter
is non-durable and rolls transactions back from deep-copied state. The Neo4j
adapter implements the protocol with parameterized Cypher, validated graph
identifiers, canonical work-property enforcement, and migration-store support.

The default suite does not require Neo4j. A live smoke target is opt-in through
`DEVGRAPH_TEST_NEO4J=1` and operator-supplied Neo4j environment variables.

## Verification

```bash
uv run pytest tests/storage tests/integration/test_neo4j_migrations.py \
  tests/integration/test_neo4j_readiness.py -q
```
