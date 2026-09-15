# Neo4j readiness operations

Readiness is a read-only composition check. `/live` is process-only and does not imply storage, migration, backup, restore, deployment, or production readiness. `/ready` is fail-closed and does not prove deployment. Disposable evidence is synthetic and not production evidence; external evidence remains `NOT_RUN_OPERATOR_INPUT_REQUIRED`. There is no RPO and no RTO claim.

## Prerequisites

- Application composition is configured with canonical storage and a read-only migration status seam.
- Migration manifest and journal compatibility window are known.
- No startup/readiness path is authorized to mutate schema, graph rows, or journal state.
- Operator evidence excludes credentials, addresses, personal paths, raw records, and raw exceptions.

## Liveness

```text
GET /live
```

Expected safe output: HTTP 200 when the process is alive. This endpoint is process-only. It says nothing about canonical storage or migration cleanliness.

## Readiness

```text
GET /ready
```

Expected safe output is HTTP 200 only when canonical storage is reachable, migration status is clean and compatible through v24, and every canonically labelled row passes identity, kind/label, archive, and canonical property inspection. v24 is the unique principal-scoped `EventReceipt` claim constraint required before authenticated writes are enabled.

Expected fail-closed output is HTTP 503 with a fixed reason such as `canonical_storage_unavailable`, migration dirty/incompatible, checksum mismatch, lock/unknown consistency, missing canonical identity/archive fields, label/kind mismatch, multi-label ambiguity, archive inconsistency, or malformed canonical properties.

Readiness and startup must not run migrations, repair rows, clear locks, retry DDL, or convert invalid data. Use the explicit migration runbook for operator-authorized changes.

## Restored-target sequence

After offline load, keep the attempt not-ready with `post_restore_verification_required`. Verify artifact integrity, storage connectivity, clean migration status, fixture equality, 23 constraints, canonical/archive round trips, then call readiness. Any failed check leaves the attempt not-ready.

## Stop conditions

- `/live` is being interpreted as storage readiness.
- `/ready` would need to mutate, auto-migrate, hide malformed rows, or expose raw exceptions.
- Canonical storage is unavailable or migration/schema/data consistency is unknown.
- A production or external target must be guessed.

Record only HTTP status, boolean readiness, fixed reason, bounded duration, and disposable cleanup. `NOT_RUN_OPERATOR_INPUT_REQUIRED` is the only valid external status until an operator runs the check.

## Evidence boundary

Current evidence proves decision logic and a disposable Neo4j readiness lifecycle. It does not prove deployment, availability, production behavior, no RPO, or no RTO. Memory/static tests cannot establish canonical Neo4j readiness.

## Sources

- [Neo4j Operations Manual 5.26 — offline backup](https://neo4j.com/docs/operations-manual/5/backup-restore/offline-backup/) — Retrieved: 2026-07-20.
- [Neo4j Operations Manual 5.26 — restore a dump](https://neo4j.com/docs/operations-manual/5/backup-restore/restore-dump/) — Retrieved: 2026-07-20.
