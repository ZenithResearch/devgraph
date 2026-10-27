# Capturing native coding-session provenance

The source-owned model and boundaries are in
[session-references.md](../../ontology/session-references.md). These commands are
opt-in local capture, not automatic DevGraph writes or live Pulse integration.
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
