# Service boundaries

## Owned by this repository

- Canonical ontology source and immutable release bundle.
- Work-graph domain models, validation, lifecycle, and relationships.
- Memory and Neo4j storage adapters.
- Scoped authorization facade, local read capability, and strict native signed
  Work/Arena receivers. Development verifiers are fixture-only.
- Export/redaction policy, unsigned transactional records, and local outbox dispatch.
- Thin FastAPI adapter, bounded Work/Arena client, monitor projection, and official
  operator frontend.
- Migration, backup artifact, restore preflight, and disposable Neo4j
  operations tooling.
- Per-user macOS configuration and launchd lifecycle, with operator-selected
  data roots and narrow document preview roots.
- Codex skill/plugin and Hermes skill/plugin packages. The Hermes adapter calls
  the installed CLI; it does not own identity, policy, or Work semantics.

## External responsibilities and limits

- Public/client portal or public ontology-site deployment.
- Wallet key custody and secS policy issuance remain native external components.
  The Devgraph CLI composes their versioned interfaces and manages explicit
  signer references and reviewed grant plans.
- Direct client access to Neo4j; clients enter through service/API operations.
- Matrix, Nostr, Dregg, Hermes runtime ownership, GitHub synchronization, webhook,
  or external event-delivery integrations.
- A public hosted endpoint, cross-platform service manager, or universal
  availability, backup schedule, RPO, or RTO guarantee. These require evidence
  from the specific deployment.
- Repository, Release, Commit, Deployment, or Deliverable as generic work
  resources.

The ontology vocabulary is broader than runtime storage and HTTP resources.
An ontology class does not become a runtime label or route unless the code
implements that binding.

The memory-backed local app is an official development fixture and UI host. It
is not durable storage, a production service, or proof that any private host is
running.

## Arena runtime and directory vocabulary

Ontology v0.5.0 introduced Arena as vocabulary; v0.6.0 activates its runtime
contract, API/CLI, migration 26, and monitor controls. Arena is outside Todo and
contains parentless Initiatives and Tasks, with descendants inheriting their
root's membership. Signed Arena grants are explicit, not implied by membership.
`Entity`, `Person`, `Agent`, and `Organization` remain directory vocabulary;
`Actor` is a compatibility name. See [the runtime contract](../ontology/arena-runtime.md).
