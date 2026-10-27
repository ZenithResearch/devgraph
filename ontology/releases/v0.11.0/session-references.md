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

This release implements models, storage seams, constraints, a local capture CLI,
and an outbox consumer. It does **not** register session mutation/read HTTP routes,
expand Wallet/secS permissions, add a background history scanner, enable a Hermes
plugin observer, or deploy/migrate an installed host. Existing signed Work/Arena
operations cannot be used to smuggle session writes. Receiver authorization,
bounded scope-aware reads, installed source resolution and Pulse wiring require
the coordinated runtime integration before this can be called connected.

The new migrations are forward-only. They add SessionRef/SessionLink uniqueness
without modifying legacy Hermes constraints or historical release bytes. Take
the normal reviewed backup/migration path on deployment; do not downgrade a
migrated host by deleting its journal. Reading a reference never permits opening
private source content through a new authority or starting a resume.

Missing/deleted/ephemeral/denied/unresolved source records stay distinct. A stored
file or ended_at=null is not proof of current activity. Grok/other harnesses have
the general representation and importer seam; no Grok collector or source API
is asserted. Only Codex CLI and Claude Code print mode have capture adapters here.
