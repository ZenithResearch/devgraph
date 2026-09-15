# Technical reference

## Package architecture

```text
frontend HTML
    │ same-origin HTTP
FastAPI routes ──► AuthorizedWorkGraph ──► domain repositories/services
    │                    │                          │
    │                    └─ exact-scope audit       ▼
    └─ IdempotentWriteExecutor ──► EventOutbox ──► GraphStorage
                                                       ├─ MemoryGraphStorage
                                                       └─ Neo4jGraphStorage
```

The API factory receives an `ApiServices` object. Routes do not construct
storage, credentials, policy, or lifecycle services. Authorization is checked
again by the facade operation used by each route. HTTP mutation execution also
verifies the credential to obtain receipt authority context.

## Domain model

`WorkObject` is a frozen dataclass with these persisted fields:

| Field | Type and rule |
|---|---|
| `id` | validated stable string identifier |
| `title` | string |
| `description` | string, default empty |
| `status` | `draft`, `review`, `accepted`, or `archived` |
| `created_at`, `updated_at` | timezone-aware datetimes |
| `version` | positive integer; starts at 1 |
| `artifact_ids`, `external_link_ids` | tuples of validated identifiers |
| `priority` | signed integer |

The Python model defines `Todo`, `Proposal`, `Initiative`, `Project`, `Issue`,
`Task`, `Requirement`, `AcceptanceCriterion`, `Blocker`, `Decision`, `Handoff`,
`ReviewPacket`, and `Milestone`. Only the five kinds listed in the API reference
are generic HTTP resources.

Generic status transitions are `draft → review`, `review → draft`,
`draft → accepted`, and `review → accepted`. Archive has its own operation.
Accepted and archived states are terminal. A Proposal cannot use the generic
transition to become accepted; `ProposalLifecycle.accept_proposal` requires one
valid `Decision` and stores `ACCEPTED_BY_DECISION`. Only an accepted Proposal
with unambiguous Decision provenance can convert to an Issue.

## Repository and relationships

`WorkObjectRepository` provides create, get, deterministic query, compare-and-
set update, omission-preserving content update, archive, and generic status
transition. It supports the five generic work kinds and validates canonical
properties on reconstruction.

`RelationshipGraph` stores and traverses the implemented relationship types:
parent/child, blockers, dependencies, requirements, acceptance criteria,
handoffs, review packets, proposal acceptance, and conversion provenance.
`EffectivePriorityService` resolves inherited priority through work parentage.

Artifacts, external links, and `SyncShadow` are typed metadata models. They do
not introduce Repository, Release, Commit, Deployment, or Deliverable work
objects. `InitiativeObservation` is stored as label `Artifact` with
`role=initiative_observation`; its dedicated repository is append-only.

## Storage

`GraphStorage` defines node CRUD/archive, edge creation/listing, deterministic
query, canonical-persistence inspection, transactions, and health.

`MemoryGraphStorage` deep-copies state at transaction entry and restores it on
failure. It is process-local and intended for tests and local fixtures.

`Neo4jGraphStorage` implements the same graph protocol with parameterized
Cypher, validated labels/relationship names, canonical work-property checks,
transaction state, health, and migration-store behavior. Configuration comes
from `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` or
`NEO4J_PASSWORD_FILE`, and optional `NEO4J_DATABASE`.

## Authorization

The exact scopes are:

| Category | Required scope |
|---|---|
| read | `devgraph.read` |
| write | `devgraph.write` |
| admin | `devgraph.admin` |
| internal export | `devgraph.export.internal` |
| redacted/summary export | `devgraph.export.redacted` |
| tool use | `devgraph.tool.use` |
| skill use | `devgraph.skill.use` |

Scopes do not imply each other; `devgraph.admin` satisfies only the admin
category. The verifier returns an `AuthorityContext` carrying actor, session,
correlation, and scopes. Successful facade calls append safe identifiers to an
in-memory `AuditLog`; denied calls do not append a successful audit record.

The included `LocalDevVerifier` is enabled only when its mode is exactly
`local-dev`. It accepts only explicitly registered credential strings, checks
expiry and audience, and otherwise fails closed. No network-location or
localhost bypass exists.

The private local host instead selects `LocalReadCredentialVerifier` in
`local-read` mode. Its owner-only registry stores a digest and fixed authority
claims for one random capability. Verification derives exactly
`devgraph.read`; the format cannot carry write, admin, or export scopes and is
not a canonical identity credential.

## Mutation receipts and idempotency

`IdempotentWriteExecutor` verifies the credential and invokes
`EventOutbox.record_mutation_with_receipt`. The outbox derives a domain-
separated SHA-256 claim from issuer, audience, actor, and raw key. The claim,
mutation, `EventReceipt` node, and `EMITTED_EVENT` edge share one storage
transaction.

Idempotency is pinned to `(issuer, audience, actor_id, raw_key)` for claim
identity and `(operation, subject_label, subject_id)` for claim scope.
A same-scope duplicate returns the original receipt without invoking the
mutation. Reusing the digest for another operation or subject fails with a 409
scope conflict. Session changes preserve retry identity; principal changes do
not. The raw idempotency key is not stored.

Receipt states are `pending`, `retry_scheduled`, `dispatched_dry_run`, and
`failed`. The only dispatcher performs deterministic local dry runs with
exponential retry scheduling; it has no external adapter or I/O.

## Exports and redaction

The HTTP API exposes three of the four policy modes:

- `internal`: full serialized records, including private records, under the
  internal-export scope;
- `redacted`: excludes private records and strips/redacts configured sensitive
  fields and references;
- `public_safe_summary`: counts only, including total, kind counts, and private
  record count;
- `denied`: policy-library mode that raises rather than returning records; it
  has no HTTP route.

Export-by-ID reads are authorized as export operations, not ordinary reads.
Missing IDs fail the whole request; order and duplicates are preserved.

## HTTP and errors

FastAPI request models forbid unknown fields. Data responses are typed Pydantic
envelopes. Problems use `application/problem+json` and an RFC 7807-compatible
body with `type`, `title`, `status`, `detail`, and optional `correlation_id`.
Validation errors include field locations and error kinds, not rejected input
values. Credential and idempotency material is never intentionally echoed.

The app disables generated OpenAPI, Swagger UI, and ReDoc by setting their URLs
to `None`.

## Monitoring and frontend

The monitor snapshot projects five work kinds, initiative-observation
artifacts, local event receipts, counts, up to 16 recent activity records, and
safe topology nodes/edges. Titles and storage health detail pass through the
redaction helper. Generic private artifacts are absent. Edges appear only when
both endpoints are present in the projection.

The official frontend is a dependency-free HTML/CSS/JavaScript document served
at `/` and `/monitor`. Its 3D SVG scene, force layout, tooltips, drag/orbit
interaction, selection styling, and per-relationship force controls operate in
the browser only.

The API has one separate exact PoP receiver for
`devgraph.monitor.view.read.v1`. A secS-signed session binds the fixed receiver
audience, loopback origin, policy, and one ephemeral page Ed25519 key. A
canonical page-key signature then binds each GET to the signed-session digest,
literal path/query, timestamp, nonce, and empty-body digest. The verifier
delegates only to the monitor snapshot builder and never creates a generic
credential or read scope. Its owner-private replay store atomically rejects
used proofs across restart,
prunes expired claims, records a durable clock high-water mark, and fails
closed at its fixed bound or on backward clock movement. A permanent lock file
serializes canonical same-directory atomic replacements, and rejected authority,
duplicate, capacity, or rollback requests leave replay bytes unchanged. See
`docs/monitor-proof-of-possession.md` for the byte contract and current
frontend-producer limit.

## Observability

The observability package implements:

- strict `HermesSessionRef` identity and pinned-field validation;
- `ATTRIBUTED_TO_HERMES_SESSION` attachment through graph storage;
- a generic ingestion seam and manual-import adapter for normalized synthetic
  observations;
- correlation-carrying standard-library logging with redaction;
- a tracing protocol plus no-op tracer.

It does not connect to a Hermes runtime, telemetry backend, or trace exporter.

## Operations and readiness

Migration code loads a checksummed forward-only manifest, uses an ownership and
journal protocol, detects dirty/mismatched state, and never auto-applies from
readiness. `/live` reports process liveness. `/ready` returns 200 only when the
configured storage and optional migration state are acceptable; otherwise it
returns a safe 503 reason.

Backup code builds and verifies canonical manifests with file sizes and SHA-256
digests. Restore code performs receiver-bound preflight, exact confirmation,
target revalidation, staging, backend execution, and explicit post-restore
checks. The provided offline backend and scripts are bounded by the repository
runbooks to disposable synthetic Neo4j Community named-volume evidence.

## Ontology publication

`ontology/` is the source tree. `scripts/build_ontology_bundle.py` deterministically
checks the current immutable `ontology/releases/v0.6.0` bundle while retaining
`v0.1.0` through `v0.5.0` byte-for-byte. The published ontology
vocabulary is broader than the five generic API resource kinds; ontology
classes do not automatically create runtime routes or labels.

v0.4.0 introduced the non-runtime Rolodex vocabulary `Actor`, `Person`, `Agent`, and
`Organization` plus descriptive membership, operation, assignment, ownership,
and attribution predicates. These terms do not change authentication, runtime
storage, Work kinds, or API routes.

## Exact secS Issue-create consumer

`devgraph.auth.secs_issue_create` consumes only
`devgraph.issue.create.v1`. Its inputs are raw request JSON bytes, the exact raw
idempotency key, raw signed projection JSON bytes, and receiver-owned
configuration/clock. It has no bearer credential, generic `AuthorityContext`,
caller-selected scope, operation, Work kind, route, handler, opcode, or
transport seam.

After strict JSON/JCS, safe-integer, canonical base64url, policy/key lifecycle,
and direct Ed25519 verification, the adapter privately registers an immutable
single-use session whose only public mutation is `create_issue()`. The adapter
constructs the canonical repository and outbox over one supplied storage
instance. Request digest extends duplicate scope without changing the v0.2
principal claim identity; raw-key SHA telemetry remains distinct from the
legacy digest field. Verification denials and conflicts have no graph or audit
side effects.

## Arena runtime v0.6.0

v0.5.0 adds canonical `Arena` and `CONTAINS_WORK` vocabulary for Initiatives
and standalone Tasks. It also prefers the Rolodex name `Entity` while retaining
`Actor` as an equivalent compatibility name. v0.6.0 activates Arena as a
separate runtime type with signed operations, versioned root membership,
authorized reads, CLI commands, and monitor details. The five Work kinds and
their stored parentage remain unchanged. The generated
bundle includes the normative [Arena profile](../ontology/arena-contract.json)
and [containment rules](../ontology/arenas.md). Historical bundles are byte-pinned.
The [runtime contract](../ontology/arena-runtime.md) defines the wire schema,
atomic transitions, migration 26, bounds, and explicit grant extension.
