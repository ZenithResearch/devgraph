# Neo4j backup and restore operations

This runbook describes the bounded `neo4j-community-5.26-offline-dump-v1` adapter and its operator CLIs. The committed proof uses synthetic data and disposable Docker named volumes. It is not production evidence. A production target or external target is unsupported here and remains `NOT_RUN_OPERATOR_INPUT_REQUIRED`. There is no RPO and no RTO claim.

## Prerequisites

- Python 3.10 environment synchronized from the lockfile.
- Docker daemon able to run the digest-pinned Neo4j Community 5.26 image.
- Source and target represented by Docker named volumes; mounts use `volume-nocopy`.
- Source stopped gracefully and proven stopped before backup.
- Target newly provisioned, empty, and labelled by the receiver as `disposable_synthetic` with a unique authority ID.
- Artifact directory empty, private, and outside the repository.
- Operator understands that failed/partial targets must be discarded, not repaired or retried automatically.

Never use a production target, external target, personal path, credential, or generated dump inside the repository.

Set portable shell variables for the disposable resources and metadata. Do not paste literal
angle-bracket placeholders into a shell. The receiver-created target volume must carry exactly:

```text
devgraph.restore.classification=disposable_synthetic
devgraph.restore.receiver=neo4j-community-5.26-offline-dump-v1
devgraph.restore.authority=<unique-synthetic-authority-id>
```

## Backup dry run

Use the portable variables defined for the disposable run:

```bash
uv run python scripts/devgraph_backup.py \
  --data-volume "${SOURCE_VOLUME}" \
  --artifact-directory "${ARTIFACT_DIR}" \
  --artifact-id "${ARTIFACT_ID}" \
  --source-database-id "${SOURCE_DATABASE_ID}" \
  --created-at "${CREATED_AT_UTC}" \
  --neo4j-version "${NEO4J_VERSION}" \
  --migration-current 24 \
  --migration-minimum 1 \
  --migration-maximum 24 \
  --source-stopped \
  --dry-run
```

Expected safe output: `backup_plan_clean`. Dry run writes neither target data nor artifact payloads.

## Offline backup execution

Remove `--dry-run` only after the source stop is independently proven. The adapter executes fixed no-shell operations equivalent to:

```text
neo4j-admin database dump --to-path=/backups neo4j
```

The backup goes first to a receiver-owned temporary named volume, then fixed binary `cat` output is written through anchored no-follow artifact descriptors. A successful artifact contains canonical manifest bytes, payload size/SHA-256, migration window, source identity, backend/version, and completion state.

## Restore preflight

```bash
uv run python scripts/devgraph_restore.py \
  --artifact-directory "${ARTIFACT_DIR}" \
  --target-volume "${TARGET_VOLUME}" \
  --dry-run
```

Expected safe output: `restore_preflight_clean`. Preflight verifies canonical manifest bytes, exact file set, checksums, backend/version/edition/media type, migration window, encryption capability, distinct target identity, receiver authority, emptiness, and capacity. It uses direct non-root read-only probes and must not mutate target state.

Stop on `target_not_empty`, `wrong_restore_target`, `artifact_checksum_mismatch`, `incompatible_database_version`, `unsupported_backup_encryption`, `target_classification_unsupported`, or any other non-clean reason.

## Interactive restore execution

Run the same command without `--dry-run` from a TTY. Non-interactive input fails with `interactive_restore_required`. Type the exact confirmation binding artifact ID, manifest SHA-256, and target logical ID.

The adapter imports the already verified bytes into a temporary named volume and executes fixed no-shell load semantics:

```text
neo4j-admin database load --from-path=/backups neo4j
```

`--overwrite-destination` is forbidden. The target must be empty. A backend failure or interruption remains failed/not-ready; discard and recreate the disposable target or request independent inspection. Do not auto-retry.

A successful load is still not readiness success. Expected output is `post_restore_verification_required` and CLI exit 3 until post-restore verification completes.

## Post-restore checks

Start only the restored disposable target and verify all of:

1. artifact bytes and manifest still verify;
2. canonical storage connectivity succeeds;
3. migration status is clean through v24;
4. expected fixture IDs/counts and canonical round trips match;
5. all 23 application constraints match;
6. archive representation and signed bounds match; and
7. `/ready` returns ready only after every preceding check.

Record only redacted versions, digest, durations, counts, checksums, fixed reason codes, and cleanup state.

## Stop conditions

- Source is running, target is not empty, target lacks receiver-held disposable authority, or source/target identity is ambiguous.
- Image digest/version cannot be proven, capacity is insufficient, artifact bytes change, or any command needs a shell or arbitrary template.
- Any step requests overwrite, an external destructive restore, secret output, production orchestration, schedule, retention policy, RPO, or RTO.

## Evidence boundary

The repository proves one disposable synthetic source-to-distinct-target lifecycle. It does not prove deployment, production backup, an external restore, availability, retention, or disaster recovery. External evidence stays `NOT_RUN_OPERATOR_INPUT_REQUIRED` until independently authorized and executed.

## Sources

- [Neo4j Operations Manual 5.26 — Back up an offline database](https://neo4j.com/docs/operations-manual/5/backup-restore/offline-backup/) — Retrieved: 2026-07-20.
- [Neo4j Operations Manual 5.26 — Restore a database dump](https://neo4j.com/docs/operations-manual/5/backup-restore/restore-dump/) — Retrieved: 2026-07-20.
