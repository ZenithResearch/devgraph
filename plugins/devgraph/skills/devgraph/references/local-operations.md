# Local operations and credentials

## Discover this user's installation

```sh
devgraph local config
devgraph local status
devgraph local read-credential status
devgraph ontology
```

The persisted local config is under the current account's
`~/Library/Application Support/Zenith/Devgraph/local.json`. It contains paths
and schema version, not seed bytes. Use the actual returned paths. API and Bolt
listeners are fixed to loopback; the authenticated CLI targets
`http://127.0.0.1:8080`. A status-only base-URL option does not redirect credentialed
queries. This integration does not configure remote hosts, tunnels, or cloud
deployment.

A ready managed host has live Neo4j/API services, successful migration readiness,
and HTTP 401 on protected reads without a credential. `/live` alone is not
readiness. An in-memory demo on port 4174 is a separate synthetic fixture;
the CLI's credentialed local queries do not target it.

For a failing host, inspect a bounded relevant tail:

```sh
devgraph logs api --stream stderr --lines 50
devgraph logs neo4j --stream stderr --lines 50
```

Review logs before reproducing them in a message; they may contain private data.
Distinguish unavailable storage, migration holds, authorization failure, and a
missing service. Do not call a read failure an empty graph.

## Read access and signing identity

| Material | Custody and meaning |
|---|---|
| Local read bearer | Owner-private `<data-root>/devgraph/credentials/devgraph.read`; exactly devgraph.read |
| Read verifier | `<data-root>/secrets/devgraph.read.v1/credential-registry.json`; digest and fixed claims |
| Signer reference | Per-user Devgraph auth profile; selected seed file path and public pin |
| Identity seed | Operator-selected raw Ed25519 seed, opened by native Castalia Wallet |
| Authority verifier/replay state | Native secS owns it; separate from the Wallet identity |
| Receiver trust | Public policy/key pins under the configured Devgraph data root |

The CLI reads the bearer internally. `devgraph local read-credential show`
reveals it only for an explicitly requested trusted local reader; prefer direct
local transfer rather than putting it in conversation text. Rotation is explicit
(`local read-credential rotate`) and invalidates the old bearer on its next use.
Normal status/start/restart does not rotate it. The browser's bearer remains a
read capability; browser possession confers no mutation rights.

`devgraph auth status --check` asks Wallet to verify the selected key and pin.
`devgraph auth work status` checks actual current grants. Identity possession,
service readiness, and mutation authority are three separate results.

For an explicitly requested new identity, use `devgraph auth key create`; it is
create-only. For an existing identity, use `auth key inspect --key-file PATH`,
then `auth setup --key-file PATH --public-key HEX`. Do not replace a lost or
unavailable identity implicitly. `auth forget` removes the reference, not the
key or grants. The current native signer accepts a raw 32-byte Ed25519 seed;
it does not convert encrypted vaults, PEM, hex text, or browser custody.

`DEVGRAPH_SIGNING_KEY_FILE` and `DEVGRAPH_SIGNING_PUBLIC_KEY` can explicitly
override the saved profile as a complete pair. No `.env` is sourced by the CLI.
Never put seed bytes into environment variables or request files. Codex and
Hermes using the same profile act as the same authenticated principal; naming
two agents does not create separate authority. The Hermes adapter deliberately
drops inherited signer environment overrides and uses the saved account profile;
its plugin settings do not select a signing identity.

Owner-private directories/files use modes 0700/0600. These permissions are
not encryption. Do not claim Keychain or encrypted-vault custody. Only operator
intent authorizes creating or extending a grant. The normal sequence is
`auth work plan --output-file FILE`, inspection of that exact plan, then
`auth work apply --plan-file FILE`. Grants expire and do not automatically renew.
Use `auth work renew`, `rotate-verifier`, or `revoke` only for the corresponding
authorized administration. Arena extension is described in the Work reference.

## Lifecycle and recovery

`devgraph local start`, `stop`, and `restart` change service state. Use them for
requested lifecycle work or a necessary reversible step in authorized recovery.
Start admits Neo4j, applies packaged forward-only migrations, and admits the API
only after migration success. Restart stops both services and checks that the
tracked database process exits before starting another instance.

An unloaded launch job can leave a still-running database process. Missing PID
files do not prove exit. A shutdown timeout or unmanaged process is a hold;
inspect bounded process evidence. Do not force-kill, delete lock/store files,
clear migration ownership, repair a volume, or restore over the live graph as
an automatic workaround.

New configuration needs an explicit existing data directory and verified
Python, Neo4j, and Java runtimes. `local configure --data-root PATH` records that
choice. A separately mounted data disk requires `--require-mounted-volume`.
Reconfiguration requires stopped services and explicit `--replace`; it neither
copies nor migrates existing data. Consult the distribution's host runbook for
runtime installation and ownership prerequisites before configuring a host.

Use `local recovery status` / `local recovery verify` for existing recovery
evidence. A backup is not a verified restore; restored authority is not current
authority. Restore drills require an isolated empty target. Do not claim public
service readiness, encrypted backups, RPO/RTO, or external receipt delivery from
source tests or a passing liveness probe.
