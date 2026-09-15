# Neo4j migration operations

This runbook is for explicit operator use against an approved database. Repository tests and the disposable Neo4j proof are local synthetic evidence; they are not production evidence. External execution remains `NOT_RUN_OPERATOR_INPUT_REQUIRED` until an authorized operator supplies the target and records redacted evidence. There is no RPO and no RTO claim.

## Prerequisites

- Use the repository's Python 3.10 environment and an explicitly configured canonical `MigrationStore`.
- Confirm the target classification and operator authority outside this repository.
- Confirm the committed manifest and migration payloads are unchanged.
- Take and independently verify an approved pre-migration backup before `apply`.
- Never put credentials, connection strings, personal paths, or raw exceptions in evidence.

## Read-only status

Run from the repository root through the configured storage seam:

```bash
uv run python scripts/devgraph_migrate.py status
```

Expected safe output contains only `ready`, `reason`, `manifest_schema_version`, `minimum_schema_version`, `maximum_schema_version`, `current_applied_version`, and `applied` entries containing `version` plus `checksum_prefix`. Status must not run migrations or repair state.

Stop for `migration_lock_busy`, `checksum_mismatch`, `schema_definition_mismatch`, or any `operator_hold_` reason. `recoverable_ddl_not_applied` permits a later explicit operator-invoked retry of the same pinned migration; it is not an automatic retry.

## Explicit apply

```bash
uv run python scripts/devgraph_migrate.py apply
```

The service must not auto-migrate at startup. Application is forward-only. There is no down migration, expiry, lock stealing, force unlock, automatic rollback, or automatic dirty-state cleanup.

Expected safe output is a clean terminal status with fixed reason codes. Re-running an entirely applied manifest should be a no-op success. A failed or ambiguous journal/schema state is an operator HOLD, not permission to mutate.

## Stop conditions

- The bootstrap constraint, version-0 owner, journal checksum, or schema definition cannot be proven exact.
- The database is unavailable, behind/ahead of the compatibility window, dirty, or ambiguously owned.
- A command would require a force unlock, DROP/RENAME, edited applied payload, secret output, or guessed target.
- The required pre-migration artifact has not been verified.

On stop, preserve fixed redacted reason codes and request independent operator review. Recovery is a new forward migration or restore of a verified backup into an approved target.

## Evidence boundary

Disposable proof records image/version, fixed reasons, migration versions/checksum prefixes, and cleanup. External target evidence must be labelled `NOT_RUN_OPERATOR_INPUT_REQUIRED` unless the operator actually ran it. This procedure does not establish a deployment, availability, production readiness, no RPO, or no RTO guarantee.

## Sources

- [Neo4j Operations Manual 5.26 — offline backup](https://neo4j.com/docs/operations-manual/5/backup-restore/offline-backup/) — Retrieved: 2026-07-20.
- [Neo4j Operations Manual 5.26 — restore a dump](https://neo4j.com/docs/operations-manual/5/backup-restore/restore-dump/) — Retrieved: 2026-07-20.
