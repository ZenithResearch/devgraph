# Ontology implementation

Human-readable source, JSON-LD, operation/repository/discovery contracts, Neo4j
constraints, publication metadata, and immutable releases `v0.1.0` through `v0.4.0` live under
`ontology/`. The release bundle is deterministic and must remain byte-stable.

Ontology vocabulary does not automatically become a runtime label or API
resource. The generic HTTP kinds remain `Proposal`, `Initiative`, `Project`,
`Issue`, and `Task`.

## Verification

```bash
uv run python scripts/build_ontology_bundle.py --check
uv run pytest tests/docs/test_ontology_publication.py \
  tests/docs/test_ontology_boundaries.py -q
```
