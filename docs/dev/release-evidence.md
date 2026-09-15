# Release evidence

Repository evidence consists of the locked Python environment, full tests,
documentation verification, Ruff checks, whitespace checks, generated backup
cost-table check, and immutable ontology-bundle check. CI runs the same core
gates on pull requests and pushes to `main`.

This evidence says nothing about a deployed service, live Neo4j target,
production authority, availability, RPO, RTO, or external integration.

## Verification

```bash
uv run pytest -q
bash docs/dev/verification.md
uv run ruff check src tests scripts
git diff --check
```
