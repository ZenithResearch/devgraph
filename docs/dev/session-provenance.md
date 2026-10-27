# Capturing native coding-session provenance

The source-owned model and boundaries are in
[session-references.md](../../ontology/session-references.md). These commands are
opt-in local capture. The read-only companion below exposes source observations
to Pulse without changing canonical Work or graph attribution.
Use a stable source-instance ID for the actual source host/profile/store. Give
different stores different IDs even if they contain the same external session ID.
Do not use machine paths, credentials or provider URLs as identifiers.

Example direct Codex launch (source CLI must be installed and authorized):

```sh
devgraph sessions run --client codex --source-instance local-codex-primary \
  --journal-dir /absolute/private/session-outbox --work Task/example \
  -- exec --sandbox read-only 'Review the selected change; do not edit files.'
```

Example Hermes-delegated Claude launch:

```sh
devgraph sessions run --client claude-code --source-instance local-claude-primary \
  --journal-dir /absolute/private/session-outbox --attempt-id example-attempt-1 \
  --parent-hermes-id hermes-conversation-1 --hermes-source-instance local-hermes-primary \
  --tool-call-id call-example --work Issue/example \
  -- -p 'Review the selected change without editing files.' --permission-mode plan
```

The wrapper adds structured output only. It never adds approval bypasses or
changes the caller's sandbox/model/authentication settings. Codex must use exec;
Claude must use print mode. Parent session and source namespace must be supplied
together. Work scope is declared, not inferred or authenticated by the journal.

For a deliberate resume, supply a **new** attempt ID and the native CLI's explicit
resume arguments. Native session identity remains unchanged. Never reuse a
launch attempt ID to retry an uncertain process launch; it is durably claimed
before spawn and reuse is refused. Automatic process recovery, tmux capture,
Grok collection and App Server lifecycle installation are not implemented here.
An App Server owner can use SessionJournal.observe with the actual thread/start
ID; capture at source is preferable to scraping projected messages.

Read the metadata outbox without changing it:

```sh
devgraph sessions events --journal-dir /absolute/private/session-outbox --limit 50
devgraph sessions events --journal-dir /absolute/private/session-outbox --after 50 --limit 50
```

Use returned sequence numbers, not assumed page lengths. The journal directory
must be owner-only and use its real path without symlink components. The database
is private, contains only reduced metadata, and is not a copy of source session
content or canonical Work. Capture failure can follow process start; preserve the
attempt and reconcile it before resuming. No automatic retry launches an agent.

The private `import_attempt` service consumes a resolved attempt only after a
receiver has obtained canonical authority and current exact Work versions. It
rejects scope substitution and rolls back all writes on conflict. No HTTP route
or CLI graph-import switch exposes it yet. Treat this as the integration seam
for the coordinated receiver/Wallet/secS rollout, not a direct-storage workaround.

Validation uses synthetic CLI executables and temporary journals. It makes no
model calls and does not install a collector or alter source histories.


## Authenticated source-session companion

`python -m devgraph.observability.session_read_runtime --config /private/config.json`
runs on **127.0.0.1:48642**, alongside the installed DevGraph host. It uses the
actual `LocalReadCredentialVerifier` and the existing owner-local `devgraph.read`
registry. Every request rechecks expiry, digest rotation and current registry
integrity; successful and denied reads have bounded private audit retention.
There is no public ingest or mutation endpoint. It does not run migrations,
replace the base host, provision grants, or accept arbitrary file/URL selectors.
The optional browser reader keeps the capability on its server side.

The owner-private config contains `data_root`, `sources` and optional `journals`.
Each source has a stable `id`, display `label`, `application` (`hermes`, `codex`,
`claude-code`) and absolute, resolved `root`. Roots are admitted by the operator,
never taken from HTTP input. Different source stores require different IDs.
Session capture must use the same source IDs to join native identities.
`journals` lists explicitly admitted `SessionJournal` directories.

- `GET /session-refs/v1?limit=100&after=sr-…` returns a bounded metadata window,
  source coverage and a next cursor. Clients compare `read_at` while paging and
  restart on a changed window. Reconciliation occurs on reads at most every 15s.
- `GET /session-refs/v1/sr-…` resolves an exact native ID at open time and resolves
  each explicit Work reference through the authenticated canonical Work HTTP API.
  It never returns a stored file path or transcript. Codex locators must remain
  within the configured sessions/archived_sessions roots and match session_meta;
  Claude matches must be unique and match the retained top-level sessionId.

These records have `canonical=false` and `canonical_graph_imported=false`.
The deterministic SessionRef ID is shared with the ontology, but listing a source
observation does not claim that its canonical node or attribution edge exists.
Source availability, explicit declared Work scope, successful Work tool results,
Work inspection and witnessed native parent/delegation relationships are separate
facts. A read relationship is **not** proof of mutation or completed work.
No relationship is inferred from a working directory, provider, title or time.
A missing end time and file modification time are never treated as heartbeats.
Native Codex turn state is projected with its timestamp and source basis.

Install the standalone `integrations/hermes/devgraph-observer` package in each
selected profile's `plugins/devgraph-observer`, then enable it using the public
Hermes plugin CLI with `--no-allow-tool-override`. Preserve existing profile
configuration and plugin settings. No credentials go in plugin config. It records
only reduced lifecycle IDs, successful typed DevGraph tool associations and
native machine-readable identity events from directly invoked Codex/Claude
terminal commands. Shell pipelines, process handles and ambiguous native stores
remain unresolved. Each profile's owner-private `devgraph-observer/events.sqlite3`
is an observation outbox, not a Work tracker. No prompt, command, tool result or
conversation content is retained. Start a **new** session to load the plugin;
existing processes remain untouched and source reconciliation covers their
retained metadata. Hook callbacks return no prompt context or tool overrides.

The installed Hermes 0.20.0 App Server path does not expose its native thread ID
through the public plugin callbacks. That join remains explicitly unavailable;
this observer does not monkeypatch Hermes or guess a parent from timing. Use the
capture wrapper for native CLI delegations requiring durable exact parent/attempt
and declared Work scope. Grok source collection and canonical signed SessionRef
write admission remain outside this read companion.

Qualification includes synthetic native stores, exact/missing/mismatched source
resolution, duplicate Claude sources, redaction, real loopback authorization and
revocation tests, write rejection, explicit Work binding and unknown outcomes.
Live source checks must report actual coverage separately from these fixtures.

### Session-aware Work CLI

The optional installed session-aware launcher enables
`DEVGRAPH_SESSION_OBSERVATION=1` for its own CLI process. After a successful exact
`query work Kind id` using the configured canonical host, it records an
owner-private `work-inspections.sqlite3` metadata witness. Failed reads, list
reads, injected test transports and alternate host configurations do not record
associations. Ordinary CLI processes are unchanged. The receiver config's
`inspection_file` admits this specific observation file.

The caller's `CODEX_THREAD_ID` or `CLAUDE_SESSION_ID` must resolve to an actual
retained session in exactly one configured native store. Mixed caller environments
are rejected. This is a resolved environment claim, not a signed caller identity.
Pulse labels the relationship `successful_cli_work_read`; it resolves the Work
record again through the canonical HTTP API. It never represents this as graph
attribution, delegation evidence or completed work. No Work body or transcript is
copied into the journal. Retained inspection timestamps can order source activity
without claiming a running process.
