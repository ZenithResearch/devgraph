# Current-state evidence and limits

This page records the combined official-frontend and private macOS deployment
baseline. Repository behavior and live machine state remain separate evidence.

## Arena runtime

The source implements Arena as a separate type with signed create, patch,
archive, and membership operations, authorized reads, CLI commands, and monitor
reading. Direct membership is stored on parentless Initiative/Task roots;
descendants inherit it through Work parents. Migration 26 adds unique Arena
IDs. Compatible native Wallet/secS binaries and an explicit Arena grant
extension are required before live writes. See the
[runtime contract](../ontology/arena-runtime.md). Installed activation and
Gallery creation require separate machine evidence.

## Named Work, operator authority and bounded Cypher (September 2026)

The current source supersedes the older Issue-only CLI coverage below. All
eleven named Work mutations and paged Work/relationship reads are available
through the CLI and HTTP; see [the contract](named-work-v1.md). The auth CLI can
create or reference a Wallet-owned signing identity and explicitly plan, apply,
inspect, renew, rotate or revoke local named Work grants. Installation alone
does not activate grants, and the local read bearer stays read-only. See
[CLI authentication](runbooks/cli-auth-setup.md).

The separate `query cypher` command and `POST /query/cypher` support a closed,
bounded read-only language over five public Work kinds. Devgraph parses and
compiles fresh parameterized read queries; it does not forward arbitrary
submitted Cypher. Whole nodes, private properties, procedures, subqueries and
mutations are unsupported. See [bounded Cypher](cypher-read-v1.md).

Production now wires private retained read/denial audit and audit-aware
readiness. Native maintenance supports daily consistent offline backups,
seven-copy managed retention, five-minute local alerts, and native-key recovery
sets on both configured local disks. Shutdown retains the original process
and unload evidence before a dump is permitted. See
[the local maintenance runbook](runbooks/local-production-maintenance.md) for
failure semantics, recovery custody, downtime, and whole-location loss
limitations. These are implementation claims; installed acceptance
and identity/grant activation need their own machine evidence.

## Evidence map

| Surface | Current evidence |
|---|---|
| Ontology | `ontology/`, immutable releases `v0.1.0` through `v0.6.0`, and `scripts/build_ontology_bundle.py --check` |
| Storage | `src/devgraph/storage/`; parity, persistence-contract, migration-store, and opt-in Neo4j tests |
| Domain model | `src/devgraph/model/`, `src/devgraph/relationships.py`, and `src/devgraph/services/priority.py` |
| Authorization | `src/devgraph/auth/` and `tests/auth/` |
| Exports/redaction | `src/devgraph/policy/` and `tests/policy/` |
| Events/outbox | `src/devgraph/events/` and `tests/events/` |
| HTTP API | `src/devgraph/api/` and `tests/api/` |
| Client | `src/devgraph/client/http.py`, `tests/client/`, and in-process integration tests |
| Observability | `src/devgraph/observability/` and `tests/observability/` |
| Operations | `src/devgraph/ops/`, `scripts/devgraph_*.py`, and `tests/ops/` |
| Frontend | `src/devgraph/frontend/`, the `/` and `/monitor` routes, and monitor API tests |
| Runtime | `src/devgraph/runtime.py`, `src/devgraph/local_host.py`, deployment tests, and the private macOS runbook |
| CLI | `src/devgraph/cli.py`, local-host tests, and the `devgraph` console entry point |

## Implemented claims

- `create_app(ApiServices(...))` constructs a FastAPI application from injected
  services. It exposes liveness, readiness, work, proposal, export,
  observation, monitor, and frontend routes.
- General data routes verify a credential through the injected verifier and
  require an exact operation scope. The monitor snapshot additionally accepts
  only its separate exact PoP contract. Static frontend HTML is public; its
  data requests are protected.
- Every HTTP mutation requires `Idempotency-Key`. The outbox stores only its
  SHA-256 digest and creates an `EventReceipt` in the same storage transaction
  as the mutation.
- All seven HTTP mutation routes authorize exactly once before duplicate
  lookup and consume one immutable graph-owned `WriteSession`; mutation,
  receipt, emitted edge, and audit attribution share that authority and
  rollback boundary.
- The exact, transport-free `devgraph.issue.create.v1` adapter verifies the
  signed secS projection against receiver-owned policy/key registries, derives
  only `devgraph.write`, and opens a single-use Issue-only graph session. It
  shares one storage instance across Issue, receipt, and edge; failed audit
  recording rolls the storage transaction back. The fixed local CLI receiver
  composes it over the configured loopback Neo4j host after canonical
  readiness checks; it is not an HTTP path or generic credential verifier.
- The exact `devgraph.monitor.view.read.v1` HTTP receiver verifies a secS-signed
  session bound to the fixed audience, loopback origin, receiver policy, and
  ephemeral page Ed25519 key, then verifies an independently signed exact GET
  request. It exposes only the existing safe monitor snapshot, records no
  receipt or outbox edge, and does not grant generic read authority. Missing
  trust configuration leaves it unavailable; malformed configuration fails
  runtime construction closed. Used proof claims persist across API restart in
  a bounded owner-private exact-operation store; current signed sessions remain
  valid only until their at-most-300-second expiry.
- Work-content PATCH additionally requires a quoted positive integer
  `If-Match`, such as `"1"`, and performs compare-and-set versioning.
- The official local app is memory-backed and read-only by credential. It is a
  development fixture, not the canonical service deployment.
- The private production composition uses Neo4j, migration-aware readiness,
  loopback-only launch agents, and one owner-provisioned local read capability.
  Its digest-only registry fixes the grant to `devgraph.read`; absent or wrong
  credentials return 401 and writes return 403. The new local installer persists
  an operator-selected data directory;
  the legacy fixed external-volume layout remains compatible.
- The bounded `devgraph` operator CLI exposes health, service lifecycle, log
  tails, ontology metadata, local-host configuration, bounded read-only Work
  queries, bounded Cypher, read-capability inspection/rotation, signer and grant
  administration, all eleven named Work operations, and the legacy exact
  secS-authorized Issue-create operation. Its legacy projection-consumer form accepts
  three bounded owner-only files. Its Wallet form invokes only the fixed
  owner-controlled secS Wallet adapter, keeps the projection in an
  owner-private temporary directory, consumes it immediately, and removes it
  after the Devgraph result. The legacy forms retain their exact-operation
  contract. Named Work also fixes the database, audience and executables;
  operator grant configuration is a separate explicit administrative flow.
- Configured lifecycle commands inspect the tracked Neo4j process separately
  from launchd. Stop waits for process exit; restart holds on a surviving or
  unknown process; start refuses an unmanaged surviving PID or a missing data
  mount. These checks do not signal an unmanaged process or prove its identity.
- Neo4j is implemented as a storage adapter and operations target. Default
  verification uses memory/in-process fixtures; live Neo4j checks are opt-in.
- External event delivery is not implemented. `DryRunEventDispatcher` updates
  local receipt state without external I/O.
- `InitiativeObservation` creation is append-only and starts with
  `authorship=inferred` and `claim_status=unclaimed`. Claiming, amendment, and
  rejection mutations are absent.
- The monitor topology contains only stored edges whose two endpoints are in
  the safe monitor projection. Layout and force controls change browser
  presentation only.

## Current limits

- No generic or federated production credential format or membership enrollment
  is present. Native Wallet key generation grants no membership.
  `LocalDevVerifier` accepts only explicitly registered
  synthetic credentials in `local-dev`; `LocalReadCredentialVerifier` accepts
  one operator-provisioned, expiring capability in `local-read`.
- No public Devgraph endpoint, generic production credential verifier, or
  writable production credential is present. The current private host is
  loopback-only. The exact monitor verifier consumes already-produced signed
  material only; it does not mint authority.
- No writable credential is registered by `devgraph.local_app`; its default
  token has only `devgraph.read`.
- No rate limiting, remote access adapter, secS network transport/runtime
  connection, Nostr, Matrix, Dregg, Hermes runtime, webhook, or background
  delivery worker is implemented. The local receivers remain transport-free.
  The legacy local CLI composition invokes the fixed secS Wallet adapter;
  named Work separately invokes the fixed native Wallet signer and secS
  verifier before submitting to HTTP. Neither installs secS into FastAPI.
- The frontend does not yet produce monitor PoP sessions/proofs. Its bearer and
  `sessionStorage` flow remains a local-development surface, and the separate
  observation-list request is not authorized by the exact snapshot receiver.
- The bounded HTTP client covers Proposal/Initiative/Project/Issue/Task Work
  creation, reads, updates, archive/status transitions, and Proposal lifecycle
  operations. It neither mints nor persists credentials, retries mutations,
  nor wraps observation/monitor routes. The local operator CLI exposes its
  get/list Work surface as `devgraph query work`; named mutations use
  `devgraph work <operation>` through the signed HTTP receiver.
- Memory storage is process-local and non-durable. Repository tests and
  disposable recovery evidence do not establish production availability,
  backup policy, RPO, or RTO.
- The repository does not claim encryption of database data at rest.
- Swagger UI and ReDoc are disabled by the app factory. OpenAPI metadata remains
  available; schema discovery does not authorize protected data or mutations.

## Machine state is separate

This repository can be correct while a private host is stopped, configured
differently, or running another commit. Runtime health must be established by
probing that host's own `/live`, `/ready`, and protected routes; repository
tests do not prove a private service is running.
