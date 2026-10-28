# Session references v1

`SessionRef` identifies a conversation owned by an application/harness, scoped by
`application`, `source_instance_id` and `external_session_id`. The source instance
is a stable opaque ID for the actual host/install/profile/store, not a hostname,
path or provider URL. Internal IDs derive from the complete identity tuple.
Providers and models do not define conversation identity: Hermes using Grok is a
Hermes session; a Grok-app conversation is a Grok session. Switching models does
not create a new conversation. Other harnesses use the same contract.

HermesSessionRef, CodexSessionRef, ClaudeSessionRef and GrokSessionRef specialize
SessionRef. New records share the SessionRef storage label and use an application
discriminator; the new specialized names do not introduce separate storage labels.
Legacy HermesSessionRef keeps its existing label, exact field shape and
ATTRIBUTED_TO_HERMES_SESSION edges. No bulk relabeling or guessed host namespace
is permitted. An explicit legacy adapter requires a caller-qualified source
instance. Old graphs/releases remain readable.

`SessionLink` is a witnessed relationship with a source event ID, attempt ID,
relation, observation time, provenance basis and two session endpoints.
CLI-supplied parent relationships are declared; source_observed requires direct
source evidence. Neither basis is cryptographic execution proof. `SESSION_LINK_ORIGIN` and
`SESSION_LINK_TARGET` retain its endpoints. `delegated_to` points parent → child;
`forked_from` points new child → original. A direct session needs no parent.
Repeated delegations to one conversation have distinct attempts/witnesses.
Resumes reuse a conversation ID and get a new attempt; forks create a new ID.
Importing authority fields remain separate from the executing agent identity.

`ATTRIBUTED_TO_SESSION` connects exact Work to a SessionRef. Attribution records
explicit declared scope and the checked Work version. It does not prove that a
session changed Work, that an output passed review, or that a task completed.
No Work status, priority, parentage or execution authority follows from a link.
One session may be associated with several Work items; no cwd/title inference or
automatic inheritance from a Hermes parent authorizes attribution.

SessionRef is an immutable first-sighting reference. Its created_at is import
time, not session start or last activity. SessionLink observed_at records the
identity observation, not a heartbeat. Metrics and lifecycle are separate
observations; old Hermes metrics keep their existing meaning. Never sum copied
Hermes projections and source-native sessions as two independent executions.

## Source capture

The explicit `devgraph sessions run` adapter persists dispatch metadata before
starting Codex/Claude, then consumes only typed native identity events. It records
declared Work scope, optional Hermes parent/tool-call ID, native identity and
process exit in an owner-private journal. It does not store commands, prompts,
stdout, tool payloads, transcripts, secrets, or provider configuration. Native
stdout is forwarded to the invoking terminal without creating a transcript file.
An exit code is process evidence, not verified task completion.

The outbox has durable monotonic sequence pages and immutable event keys. Same
event/different content is a conflict. A claimed launch attempt cannot be launched
again after a crash or unknown outcome. Resume deliberately with a new attempt
ID and explicit source-native session ID. An unresolved attempt is retained;
absence of native output never becomes a guessed session or an all-clear.

The private receiver-side importer requires its own verified AuthorityContext
and exact current versions for every declared Work binding. It imports references,
parent/child witnesses and attributions transactionally. The journal's metadata
does not supply or grant authority. Fork witnesses can be supplied through the
model, but automatic fork-parent extraction is not implemented by the launcher.

## Admission and deployment

The source candidate includes models, storage constraints, native capture,
a standalone Hermes observer and an authenticated companion reader. Deployment
of the canonical write path remains separate from source observation. Existing
Work/Arena operations keep their original payloads and cannot smuggle session
writes; existing grants are not extended by adding this operation.

The additive `devgraph.work.session.attach.v2` operation is the public signed
admission path. It names one existing Work subject and expected version, one
source identity, and optionally one exact witnessed SessionLink with both endpoints.
Every derived SessionRef/SessionLink ID must be authorized together with the Work
subject. Importing actor, authentication session and correlation come from the
verified receiver context. Old signed contracts and grants keep their original
meaning; no existing grant acquires this operation. A committed attachment bumps
Work version, preserves progress and emits the normal replayable EventReceipt.
GET `/session-refs/v1` and `/{id}` expose bounded canonical metadata through the
existing read facade; they do not open source transcripts.

Hermes upstream persists App Server `thread_id` in
`sessions.model_config.codex_thread_id`. The reader joins only when that ID resolves
to exactly one retained Codex source record. `uses_runtime` points Hermes → Codex;
it is distinct from delegation. Its witness time is the collector's first sighting
of that persisted binding, not the original native thread creation time. The
private source journal retains this time across reader restarts. Missing bindings
in older Hermes installs and historical sessions are never guessed.

`devgraph sessions prepare-import` requires an exact resolved source and an actual
recorded Work inspection or explicit dispatch scope. It reads current Work version
and creates private request, idempotency and review files. An optional `--link-ref`
selects a witnessed runtime relationship. It never signs, grants or submits.
Canonical graph status is unavailable when the running host lacks the new API;
a source observation alone is not evidence of canonical import.

The new migrations are forward-only. They add SessionRef/SessionLink uniqueness
without modifying legacy Hermes constraints or historical release bytes. Take
the normal reviewed backup/migration path on deployment; do not downgrade a
migrated host by deleting its journal. Reading a reference never permits opening
private source content through a new authority or starting a resume.

Missing/deleted/ephemeral/denied/unresolved source records stay distinct. A stored
file or ended_at=null is not proof of current activity. Grok/other harnesses have
the general representation and importer seam; no Grok collector or source API
is asserted. Only Codex CLI and Claude Code print mode have capture adapters here.
