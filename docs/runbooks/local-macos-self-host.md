# Private local macOS host

This runbook launches one private Devgraph instance and one persisted Neo4j
Community 5.26.29 instance on a Mac. Both listeners are loopback-only. Cloud,
provider, and multi-host deployment belong to Hub and are intentionally absent
from this workflow.

## What is fixed and what is configurable

The installed package owns the launch-agent renderer, migration bundle,
ontology release, and operator CLI. The operator chooses the growing data root.
It can be an ordinary existing directory or a dedicated directory anywhere on
an attached volume; the volume itself does not have to be devoted to Devgraph.

| Fixed local surface | Value |
|---|---|
| API | `http://127.0.0.1:8080` |
| Neo4j Bolt | `bolt://127.0.0.1:7687` |
| Launch agents | `ca.zenith.devgraph.neo4j`, `ca.zenith.devgraph.api` |
| Local config | `~/Library/Application Support/Zenith/Devgraph/local.json` |
| Local read capability | `<data-root>/devgraph/credentials/devgraph.read` |
| Local read digest registry | `<data-root>/secrets/devgraph.read.v1/credential-registry.json` |
| Exact secS receiver bundle | `<data-root>/secrets/secs-magik/devgraph.issue.create.v1/` |
| Exact monitor PoP receiver bundle | `<data-root>/secrets/secs-magik/devgraph.monitor.view.read.v1/` |
| Exact monitor replay state | `<data-root>/secrets/secs-magik/devgraph.monitor.view.read.v1/replay/claims.json` |
| Neo4j / Java | Neo4j 5.26.29 / OpenJDK 21 |

The config file contains paths and a schema version only. It is written with
owner-only permissions. The generated Neo4j credential remains under the
chosen data root and is never returned by CLI output. The separate read
capability is revealed only by the explicit `local read-credential show`
command; status output includes only its path and safe claims.

## Runtime prerequisite

Install the Devgraph distribution into its isolated Python environment. Place
the checksum-verified Neo4j 5.26.29 Community runtime at the default host path
or pass `--neo4j-home`. Install OpenJDK 21 at the default Homebrew path or pass
`--java-home`. The official Neo4j archive SHA-256 used by this release is:

```text
a45ca9644100d995500f7ea7f5bb4874e16e588891fdfbdff65d21321331caa2
```

`devgraph local configure` checks the Python, Neo4j, `neo4j-admin`, and Java
runtime paths before it writes the local configuration.

## macOS ownership preflight

Local credential and exact secS receiver paths trust UID and mode metadata. On macOS that metadata
is not an integrity boundary when the containing filesystem is mounted with
ownership disabled (`noowners`). Both the exact Issue-create command and the
monitor receiver detect the native mount flag and fail closed with the bounded
diagnostic `configured data-root mount has ownership disabled`. They never run
`diskutil`, change mount flags, or change permissions themselves.

For the legacy dedicated volume, an administrator must explicitly enable
ownership:

```bash
sudo diskutil enableOwnership /Volumes/Devgraph-Data
diskutil info /Volumes/Devgraph-Data | grep 'Owners:'
stat -f '%Sp %Su:%Sg %N' /Volumes/Devgraph-Data /Volumes/Devgraph-Data/secrets
ls -led /Volumes/Devgraph-Data /Volumes/Devgraph-Data/secrets
devgraph local restart
devgraph local status
```

The `diskutil` check must report `Owners: Enabled`. On this APFS layout the
actual mount root can remain immutable `root:wheel 0775`; administrator
`chown`/`chmod` may return `EPERM`. Devgraph admits that shape only when the
configured data root is exactly the native ownership-enabled mountpoint, UID
and GID are both `0`, it is not world-writable, it has no extended ACL, and
the Devgraph service has a nonzero effective UID/GID with GID `0` absent from
its supplementary groups. Failure to verify process groups denies startup.
This exception never applies below the mount root. `secrets`, `secs-magik`,
each operation bundle, replay directory, and their files must remain owned by
the effective service user, non-symlinked, free of extended ACLs, and not
group/world-writable; owner-private files also reject hard links. For an
ordinary directory or a data-root subdirectory, no root-owned exception
applies. Run the equivalent checks against the actual configured volume/data
root rather than copying the legacy path. Do not proceed to receiver
provisioning until `devgraph local status` again proves liveness, readiness,
and the unauthenticated protected-route denial.

## Configure an ordinary directory

The directory must exist so a typo cannot silently place graph data somewhere
unexpected.

```bash
mkdir -p "$HOME/Devgraph-Data"
devgraph local configure --data-root "$HOME/Devgraph-Data"
devgraph local start
devgraph local status
```

## Configure an attached volume

Point Devgraph at a dedicated directory on the volume, not at a broad root.
`--require-mounted-volume` makes configuration fail unless the directory is on
a separately mounted filesystem.

```bash
mkdir -p /Volumes/MyExternalDrive/Devgraph
devgraph local configure \
  --data-root /Volumes/MyExternalDrive/Devgraph \
  --require-mounted-volume
devgraph local start
devgraph local status
```

The older `/Volumes/Devgraph-Data` layout remains compatible, but it is an
example rather than a fixed identifier. Existing installations without
`local.json` can continue using `devgraph service ...` and the legacy script
wrappers until they are deliberately reconfigured.

## What configure and start do

`configure` creates private Neo4j data, transaction, log, secret, and backup
directories under the chosen root; writes the loopback-only Neo4j config;
creates one stable Neo4j credential plus one expiring read-only API capability;
initializes a new empty database password; and installs the two user launch
agents. It does not start a service.

`start` admits the services in dependency order:

1. load or start the Neo4j launch agent;
2. wait for the loopback Bolt service;
3. apply the package-bundled forward-only migrations;
4. load or start the API launch agent.

If storage or migration does not become safe, the API is not admitted. New
launch agents select `DEVGRAPH_AUTH_MODE=local-read`. Missing or wrong
credentials remain HTTP 401, the generated capability can perform only
protected work reads under `devgraph.read`, and all mutation attempts remain
HTTP 403. The
`fail-closed` and legacy `disabled` spellings retain denial-only behavior for
existing agents.

## Manage and inspect

```bash
devgraph local config
devgraph local status
devgraph local read-credential status
devgraph query work Issue
devgraph local restart
devgraph local stop
devgraph logs api --stream stderr --lines 50
devgraph ontology
```

The status command combines the credential-free configuration, runtime path
checks, launchd state, API liveness/readiness, and a protected-route denial
probe. Admission requires both jobs loaded, `live=true`, readiness through the
bundled migration version, and HTTP 401 from
`http://127.0.0.1:8080/work/Issue` without a credential.

To connect the browser monitor, run
`devgraph local read-credential show` and paste the value into its credential
field. To invalidate it, run
`devgraph local read-credential rotate --actor-id local-codex-reader`; the
receiver reloads the digest registry on every request, so the old value stops
working without a service restart. These commands do not reveal or rotate the
Neo4j password.

## Exact local secS Issue create

After secS has supplied the closed `receiver.json` policy binding and
`secs-public-key-registry.json` production trust registry, install them at the
fixed receiver-bundle path above with directory mode `0700` and file mode
`0600`. Install the fixed secS Wallet adapter at
`~/Library/Application Support/Zenith/secS/bin/` and keep it owner-controlled.
The complete one-shot form requires only owner-only request and idempotency-key
files:

```bash
devgraph wallet-issue-create-v1 \
  --request-file /private/operator/request.json \
  --idempotency-key-file /private/operator/idempotency-key.txt
```

The command prints the single fixed ceremony URL. After Wallet approval, it
immediately consumes the short-lived projection and returns the Devgraph Issue
and `EventReceipt` result. The temporary projection is removed before exit.

For an explicitly configured headless agent, use
[`devgraph agent-issue-create-v1`](agent-issue-create-v1.md). Its separate native
Wallet signer reads a reference to the existing Dregg identity, while the
installed secS policy still authorizes the exact Issue. The linked runbook lists
the required binaries, two environment settings, and live acceptance checks.

The lower-level receiver form also accepts an already-produced owner-only
signed projection:

```bash
devgraph secs-issue-create-v1 \
  --request-file /private/operator/request.json \
  --signed-projection-file /private/operator/signed-projection.json \
  --idempotency-key-file /private/operator/idempotency-key.txt
```

The command reads the persisted local config, fixed receiver bundle, fixed
loopback Bolt endpoint, and generated Neo4j credential file. It accepts no
database, receiver, audience, operation, or bearer-token override. It verifies
migration and canonical-data readiness before the exact consumer can mutate.
The generic HTTP Work routes retain their independent read-only local verifier;
this command does not widen that verifier or install an HTTP write capability.
See `docs/usage.md` for the receiver manifest schema and input bounds.

## Exact monitor PoP receiver

The monitor receiver directory follows the same fixed secS receiver convention
with public trust material in strict `receiver.json` plus
`secs-public-key-registry.json`. Its `replay/claims.json` is owner-private,
durable exact-operation state that records only claim digests, nonces, expiries,
and a clock high-water mark. Used proofs stay denied after API restart; current
signed sessions remain valid only until their at-most-300-second expiry. If
the trust bundle is absent, the snapshot remains closed. If
partial, symlinked, group/world-writable, or malformed, API construction fails
closed. The launch agent supplies the configured `DEVGRAPH_DATA_ROOT`; there is
no caller-selected trust path. The receiver authorizes only
`GET /monitor/snapshot`, creates no graph/outbox state, and leaves every bearer
and Work route unchanged. See `docs/monitor-proof-of-possession.md` for its
exact byte contract and the unimplemented frontend/secS/Wallet producer slice.

## Reconfiguration and stop conditions

Configured `local status` also inspects the bounded Neo4j PID file and reports
`neo4j_process` independently of launchd. A live tracked PID with `managed=false`
means the launch agent is unloaded while a process survives; the PID alone is
not proof of process identity. An unavailable or malformed PID file is an
unknown state, and a missing configured mount is a storage hold.

`local stop` waits up to 60 seconds for the tracked database process to exit
after unloading the jobs. If it remains alive, stop fails with
`neo4j_shutdown_timeout`; restart does not launch another database. `local start`
holds with `unmanaged_neo4j_process` when it finds a surviving tracked process
without its launch agent. These commands never force-kill a process or delete
PID, lock, or store files. Inspect identity and bounded logs before a separate
recovery decision. A successful filesystem verification does not establish
database consistency.

Stop the services before changing roots. A different persisted config or
launch agent is refused unless `--replace` is explicit:

```bash
devgraph local stop
devgraph local configure --data-root /Volumes/NewDrive/Devgraph --replace
devgraph local start
```

`--replace` changes configuration; it does not copy, delete, or migrate graph
data. Never erase or repartition a disk, delete graph directories, or rerun an
initial-password command as a recovery step. An unencrypted external volume
leaves its contents available to someone with physical access. Backup/restore,
RPO, RTO, public ingress, high availability, and any claim that a host is
canonical or production require separate evidence.
