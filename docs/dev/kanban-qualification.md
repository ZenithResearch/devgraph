# Kanban candidate qualification

This is follow-up work to the topology/theme UI PR (#8), on
`codex/kanban-workflows`. The source candidate implements the board, workflow
engine, migration 27, ontology v0.7.0, and named signed mutations. It is **not
qualified for installed Chrome editing** until the final gate below passes.

## Contract and source boundaries

The shared public protocol revision is
`1003cc3c7ab55ab34d66fbff8832a3ac18aa8ae2`. Existing Work vectors are unchanged;
additive workflow and flexible-parent vectors are in
`crates/devgraph-work-protocol/tests/fixtures/workflow-v1/requests.json`.
Wallet revision `d6f2b5579bce88058b1887a07a348453a522105e` signs those requests,
admits only the exact canonical Kanban document in addition to SDK preview,
and revokes a connection on navigation between them. secS and the private SDK
follow-up branches pin the same protocol and Wallet commits. The private SDK,
native host and companion implementation remain in their existing repositories.
Review the companion lockfiles for exact resolved revisions.

The persisted workflow is independent of lifecycle and parentage. Forward
migration 27 validates optional metadata and journals admission atomically; it
never fabricates stages for historical records. Historical ontology releases
v0.1.0–v0.6.0 remain unchanged. Release v0.7.0 includes the versioned workflow
catalog and expanded parent pairs. The old Work response envelope remains intact;
workflow state is exposed only through new read projections and deliberate
reader/topology additions.

## Repository checks

Run from the Devgraph source root:

```sh
uv sync --locked
bash docs/dev/verification.md
uv run ruff check src tests scripts integrations
cargo test --locked --all-features
cargo check --locked --target wasm32-unknown-unknown
git diff --check
```

Pytest includes the Node Kanban checks when Node is available. Workflow tests
cover the complete higher/execution paths, gate ordering, rework, Waiting,
revision-bound evidence, legacy absence, conversion, parentage and scoped reads.
API tests cover 250 and 1,500 items, bounded cards with complete counts,
continuation invalidation, credential separation, signed retries and conflicts.
Migration tests verify no stage backfill and malformed-metadata rejection.
Companion suites exercise real Wallet/WASM signing of the same vectors and secS
resource authorization. These establish source-level compatibility only.

## Required installed-native gate

Use a disposable macOS account/runner with disposable identity, data, read key,
explicit workflow grants and a receiver at the canonical loopback port 8080.
Build all matched candidates and record immutable revisions and binary hashes.
Install the matching Wallet extension and native host in that disposable account.
Exercise the actual `/monitor/kanban/` page through Chrome, Wallet confirmation,
native host, secS and receiver. Verify:

- Assign, review and transition, including an evidence link and exact resource grants.
- Denied or cancelled confirmation leaves the card and record unchanged.
- Navigation/disconnect and expired authority revoke access.
- Version conflict refreshes the record without retrying a different mutation.
- Interrupted dispatch preserves the original bytes/key and explicit recovery
  returns the original EventReceipt without a duplicate mutation.
- Parent approvals stay independent; stale evidence cannot clear a gate.

The current machine's canonical port is occupied by the live local service; the
new board preview runs on an independent in-memory fixture port. No production
identity, grants, native installation or data were changed for qualification.
A fixture bridge or successful WASM test must never be reported as this gate.
Until the installed test passes, keep the PR a draft and the browser-editing
release claim unqualified. Read-only preview is independently usable.
