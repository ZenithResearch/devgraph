# Operator runbook

## Repository verification

```bash
uv sync --locked
uv run pytest -q
bash docs/dev/verification.md
uv run ruff check src tests scripts
git diff --check
```

## Read-only local monitor

```bash
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open `http://127.0.0.1:4174/` and use `fake-credential-monitor`. Data is
synthetic and memory-backed. Stop the process with Ctrl-C; all state is lost.

## Neo4j operations

Do not infer target values. Read the full runbook before invoking a mutation:

- [Migrations](../runbooks/neo4j-migrations.md)
- [Backup and restore](../runbooks/backup-restore.md)
- [Readiness](../runbooks/readiness.md)

The scripts expose help without operating on a target:

```bash
uv run python scripts/devgraph_migrate.py --help
uv run python scripts/devgraph_backup.py --help
uv run python scripts/devgraph_restore.py --help
```

After supplying the documented operator inputs, the migration entry points are:

```bash
uv run python scripts/devgraph_migrate.py status
uv run python scripts/devgraph_migrate.py apply
```

Validate the committed parameterized cost table without contacting a provider:

```bash
uv run python scripts/render_backup_costs.py --check
```

The authoritative procedures are `docs/runbooks/neo4j-migrations.md`,
`docs/runbooks/backup-restore.md`, and `docs/runbooks/readiness.md`.
Repository evidence is bounded to disposable synthetic targets. External or
production recovery remains unexecuted here; no RPO or RTO is established.

If a health check fails, report the exact safe reason. Do not substitute a
local auth bypass, direct Neo4j mutation, or guessed production target.
