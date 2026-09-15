# Native local production maintenance

This runbook covers the configured macOS host. The Docker-only adapter in
`backup-restore.md` remains a separate disposable test surface. These commands
never restore or overwrite a database.

## Retained audit

Production stores operational audit under `<data-root>/devgraph/audit/events.sqlite3`.
The existing service-controlled `devgraph` subtree permits audit provisioning
when the volume root itself is intentionally not writable by the service user.
The directory must be owner-private (0700); files must be private, regular,
single-link files without extended ACLs. Mount ownership must be enforced.
SQLite commits use FULL synchronization and macOS fullfsync. Concurrent API
processes serialize writes with a five-second lock budget.

Within one API process, concurrent audit submissions share a bounded durable
transaction after at most a 10 ms grouping window. At most 16 submissions / 1,024
events may be outstanding; a transaction contains at most 256 events. Each
caller waits for its transaction to commit before a protected request can run
or its response can be sent. A failed transaction fails every participant.
Admission and commit waits are bounded to five seconds; overload or a stalled
writer fails closed with 503. An in-flight write may finish after its caller
times out, but cannot retroactively admit that request. The writer exits when
idle. Grouping reduces durable-write amplification; it does not remove storage
latency or replace investigating slow backing-device I/O.

Authorized operation records retain fixed operation/category names and SHA-256
digests of verified actor/session/correlation IDs. HTTP records retain a random
request ID, fixed route name, method, attempt/result phase, and response status.
They do not retain bodies, raw URLs, query strings, headers, credentials, signed
proofs, raw authority IDs or Work content. Denials are anonymous HTTP outcomes;
unverified claims never become an authenticated actor.

Retention is the earlier of 30 days or 100,000 events; SQLite is capped at 128 MiB
plus its rollback journal. `/ready` also checks a durable audit write and age
retention. An unavailable audit store produces readiness 503 and denies requests
before execution. A storage failure after a mutation commits can also return
503: retry with the **same idempotency key**. The atomic graph receipt remains
the authoritative mutation record. Incomplete HTTP attempts are not proof of
rollback or commit. Audit deletion is logical retention, not secure erasure.

## Schedule and commands

```text
devgraph local maintenance install
devgraph local maintenance backup
devgraph local maintenance status
devgraph local maintenance monitor
devgraph local recovery create
devgraph local recovery status
devgraph local recovery verify
```

Installation creates owner-controlled user LaunchAgents:

- `ca.zenith.devgraph.backup`: daily at 03:30 in the host's local time zone.
- `ca.zenith.devgraph.monitor`: every 300 seconds and when loaded.

The backup requires a healthy source, sufficient free space, an exclusive
maintenance lock, and proven graceful shutdown of both services. It checks and
dumps both `neo4j` and `system`, saves local receiver configuration/read
credentials and retained audit, hashes every file, and verifies the artifact.
It attempts migration-gated service readmission even after a failed dump. It
never force-kills a database. It does not overlap another managed backup.
Shutdown observation is bounded to two minutes, allowing Neo4j's own
transaction drain to finish beyond one minute without admitting a live store.
The process tracked before job unloading must exit even if Neo4j removes its
PID file early. Job unloading is checked again after that observation. The
backup result retains the initial PID/state, service unload result, final
process observation and elapsed shutdown time. A timeout or ambiguity fails
closed and never escalates to a forced stop.
The API is unavailable during the offline backup; allow a maintenance window.

Artifacts live in `<data-root>/backups/managed-v1`. Seven complete verified
backups are retained after a new backup succeeds. Unknown, partial, corrupt and
historical recovery directories are preserved for operator review. The job
needs at least 5 GiB or twice the measured store size free, whichever is larger.

The managed database artifacts remain on the configured data volume and are
not application-encrypted. After verifying each artifact, the backup now creates
complete private recovery sets at both fixed destinations:

- `~/Library/Application Support/Zenith/Devgraph/recovery`
- `<data-root>/devgraph/recovery`

Each set includes a separately verified copy of the managed database artifact,
the existing signer exported by native Wallet, and the secS verifier key,
manifest, policy, public registry and consistent replay database exported by
native secS. Devgraph never reads either private signing seed. Wallet preserves
the same public identity and uses create-only output. secS owns SQLite snapshot
consistency under its authority lock; signing holds the corresponding shared
lock. Maintenance takes its lock before invoking secS, never in the reverse
order. Policy/registry pins must match the included receiver, both copies must
have the same authority generation, and the saved signer must match the policy.

The internal copy must be on a different filesystem and physical disk from the
database. The external copy must be on a different filesystem and physical disk
from both the original signer and secS producer state. Verification uses macOS
mount metadata and physical-device identifiers; separate APFS volumes alone are
insufficient. These two local copies address loss of either attached disk,
not loss of both disks, theft of the whole setup, or a shared-account compromise.

Recovery sets use raw file custody with 0700 directories and 0600 files. They
are **not encrypted by the application** and no recovery password is generated.
Every copy of a key carries the same signing authority. Filesystem encryption,
when independently configured, is a different protection. Seven complete
verified recovery pairs are retained after a new pair succeeds. The current pair
is always protected. Unknown, partial, unpaired or damaged sets remain for
operator review; only old pairs with the exact schema, layout and verified
components can be removed. Retention failures are reported separately and leave
remaining copies in place. Space admission fails before export when either
destination is low.

`local recovery create` uses the latest verified managed database backup,
including a preserved dump from a run whose recovery export failed. It falls
back to the last successful backup only on installations without the newer
managed-backup pointer. A policy
change since that backup requires a fresh `local maintenance backup` so the
receiver pins and producer state agree. `status` reads metadata only; `verify`
checks all component hashes, native identity agreement, secS replay integrity
and the destinations' current physical-disk independence.
Neither command starts a database, installs a key, activates a grant or updates
production receiver trust. Expired/revoked authority can be preserved and
verified; its restoration remains inactive pending current authority review.
An intentionally absent receiver admission is preserved only when native secS
reports invalid current authority and every policy rule is explicitly revoked.
The manifest records `receiver_admission=absent_revoked`; absent admission for
an active or merely expired policy is rejected.

If recovery export fails, the verified database artifact is preserved and
`last-managed-backup.json` records that success. The overall backup fails with
`recovery_snapshot_failed`, keeps the previous `last-backup.json`, and still
attempts service readmission. A missing signer, missing authority or skipped
copy is never reported as complete recovery. Cross-disk latest references are
published only after both sets verify; an interrupted publication is reported
as a mismatched pair and may be repaired by creating a new complete pair.

Restoration is an explicit operator operation into a separate empty target,
followed by consistency, migration, Work/receipt and authority verification.
Do not overwrite the production store or downgrade its migration journal.

## Isolated restore verification

Start from `local recovery verify`. Select one verified set and create a new
private empty target, keeping production data/configuration untouched. Load
both `neo4j.dump` and `system.dump` from its `database/<backup-id>` directory
with the selected native Neo4j runtime. Run `neo4j-admin database check` on both
databases before startup. Use a separate loopback Bolt port and rewrite every
Neo4j data/transaction/run/log path to the isolated target.

Restore read credentials and retained audit from the database artifact privately.
Check audit SQLite integrity, compare the expected Work/relationship snapshots,
versions and EventReceipt IDs, then prove readiness and anonymous-read denial
through a production-configured isolated API. Never infer matching records from
hash verification alone. `verify_recovery_set(path)` is the package seam used by
the isolated drill for component validation; it returns public metadata only.

Wallet `--inspect-identity --key-file <set>/identity/devgraph-dregg.key` proves
the restored signer still derives the recorded public identity. Native secS
`admin verify-snapshot --input-directory <set>/authority` verifies key agreement,
policy pins, replay SQLite structure and file hashes without activating it.
Keep archived receiver trust and authority outside live installation paths.
Before write admission, reconcile revocations and expiry against current
operator policy; an old backup must never reactivate an old grant. Where
post-backup revocation history is unavailable, provision a fresh reviewed
issuer/grant generation and wait out outstanding short-lived projections.

Record source artifact, target path, native load/check results, Work/receipt/audit
comparison and actual elapsed recovery time. Terminate the isolated instance
gracefully and prove its tracked process exited and port was released. Retain
the failed target if any check fails; never substitute its data for production.

## Alerts and evidence

The monitor records its current status, actionable alert codes and timestamps
under `<log-root>/maintenance/status.json`, independently of the data volume.
It sends a local macOS notification when alert codes change or clear; delivery
errors are recorded and retried. OS notification settings can suppress display.
No third-party messages or network notification service is configured.

Alerts cover an unavailable volume, free space below 5 GiB or 10%, failed
readiness, backup age over 26 hours, a failed backup, and a backup still running
after 20 minutes. Expected brief backup downtime is suppressed while the lock
is held. `backup-result.json` retains the latest attempt and `last-backup.json`
the last fully successful backup/readmission. Native command output is not
projected into logs. Inspect fixed failure codes and bounded service logs.

The schedule targets a 24-hour local recovery point; it is not an uptime or
disaster-recovery guarantee. A sleeping, logged-out, disconnected or full host
can miss that target. Record actual dump/restore timings and source/target
verification before making any RPO/RTO claim.
