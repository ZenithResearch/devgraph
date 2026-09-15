#!/usr/bin/env bash
set -euo pipefail

# Run from the repository root:
#   bash docs/dev/verification.md

printf 'Checking repository and current-state documentation...\n'
test "$(git rev-parse --is-inside-work-tree)" = "true"
for f in \
  README.md \
  LICENSE \
  NOTICE \
  docs/user/programmers.md \
  docs/user/generalists.md \
  docs/index.md \
  docs/usage.md \
  docs/technical-reference.md \
  docs/current-state.md \
  docs/api.md \
  docs/auth.md \
  docs/events.md \
  docs/export-redaction.md \
  docs/observability.md \
  docs/monitor-proof-of-possession.md \
  docs/boundaries.md \
  docs/service-contract.md \
  docs/user/index.md \
  docs/user/operator-frontend.md \
  docs/user/operator-runbook.md \
  docs/initiative-observations.md \
  docs/runbooks/neo4j-migrations.md \
  docs/runbooks/backup-restore.md \
  docs/runbooks/readiness.md \
  docs/ops/backup-cost-inputs.json \
  docs/ops/backup-cost-table.md \
  scripts/render_backup_costs.py \
  tests/docs/test_ops_runbooks.py \
  tests/docs/test_scope_contract.py \
  tests/ops/test_backup_cost_model.py
do
  test -f "$f"
 done

printf 'Checking deployment artifacts exist...\n'
for f in \
  deploy/local/native/neo4j.conf.in \
  docs/runbooks/local-macos-self-host.md \
  scripts/configure_local_host.py \
  scripts/render_local_launch_agents.py \
  scripts/set_local_neo4j_initial_password.py \
  tests/ops/test_local_macos_deployment.py \
  tests/ops/test_local_host_config.py
do
  test -f "$f"
done

printf 'Checking current docs name executable boundaries...\n'
grep -q 'private, loopback-only production composition' README.md
grep -q 'console-script entry point' docs/usage.md
grep -q 'bounded `devgraph` operator CLI' docs/current-state.md
grep -q 'no external adapter or I/O' docs/technical-reference.md
grep -q 'does not provide a general deployment launcher' docs/api.md
grep -q 'private loopback-only production composition' docs/dev/service-boundary.md

printf 'Checking implementation surfaces...\n'
for f in \
  src/devgraph/local_app.py \
  src/devgraph/local_host.py \
  src/devgraph/runtime.py \
  src/devgraph/cli.py \
  src/devgraph/frontend/app.py \
  src/devgraph/api/app.py \
  src/devgraph/api/routes.py \
  src/devgraph/auth/verifier.py \
  src/devgraph/events/outbox.py \
  src/devgraph/model/initiative_observations.py \
  src/devgraph/storage/memory.py \
  src/devgraph/storage/neo4j.py \
  scripts/devgraph_migrate.py \
  scripts/devgraph_backup.py \
  scripts/devgraph_restore.py
do
  test -f "$f"
done

printf 'Checking generated artifacts...\n'
uv run python scripts/render_backup_costs.py --check
uv run python scripts/build_ontology_bundle.py --check

printf 'Running repository tests...\n'
uv run pytest -q

printf 'Running static checks...\n'
uv run ruff check src tests scripts

printf 'devgraph repository verification OK\n'
