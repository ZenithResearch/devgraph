# Kanban candidate qualification

This work is integrated with the shared protocol and topology/theme UI on
`codex/devgraph-complete`; [integration evidence](complete-ui-integration.md)
records the original PR heads. The source candidate implements the board, workflow
engine, migration 27, ontology v0.7.0, and named signed mutations. It is **not
qualified for installed Chrome editing** until the final gate below passes.

## Contract and source boundaries

The canonical Todo protocol and SDK are built from the same public revision.
`3454db330ec4a1b42352367652b2a0ceb5a066c4` remains the compatibility baseline. Historical Work/Arena request bytes remain
unchanged. The replacement browser path is Kanban/web SDK → generic Wallet
credential approval → Devgraph-owned HTTP transport → native secS → guarded
receiver. It does not use `provider.devgraph` or Wallet native messaging.

Merged generic Wallet #28 is `ff8de6ddc04ac2fb6b39408274276bc4ce984d42`; secS #298
adds Todo/workflow authority at `c5d81bab74820b0cd127759c5b367be471ff0d82`, depending on
#297. Public Devgraph PR #10 owns the SDK/native implementation,
release evidence harness and application transport. Browser SDK 0.2 reads take an
explicit in-memory read credential; native reads retain owner-private access.
Wallet remains optional for reading. No grant acquires new authority implicitly.

Merged Wallet starts with zero trusted browser providers. Public Devgraph's SDK
0.2.0-preview.2 and Kanban load operator-configured public pins from the same-origin
`/credential-work/v2/provider-profile` endpoint, then use separate fresh clicks to
review the provider and connect. No build-time pin injection or Wallet read bridge
is needed. Terminal issuer trust remains independently configured. Wallet's own
adapter removal and package qualification are merged; Devgraph/secS application
qualification and retirement still require the complete installed VM gate.


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

Use a clean disposable macOS VM with its own OS account, identity, data, read key,
explicit workflow grants and a receiver at the canonical loopback port 8080.
Build all matched candidates and record immutable revisions and binary hashes.
Install exact packaged candidates in that guest. Environment-variable overrides
are not isolation for secS's OS-derived paths. Exercise actual `/monitor/kanban/`
and web SDK through Chrome → generic Wallet → Devgraph HTTP → native secS → guarded
receiver, and terminal/native SDK through generic Wallet's interactive terminal
approval → native secS → receiver. Verify:

- Assign, review and transition, including an evidence link and exact resource grants.
- Denied or cancelled confirmation leaves the card and record unchanged.
- Navigation/disconnect and expired authority revoke access.
- Version conflict refreshes the record without retrying a different mutation.
- Interrupted dispatch preserves the original bytes/key and explicit recovery
  returns the original EventReceipt without a duplicate mutation.
- Parent approvals stay independent; stale evidence cannot clear a gate.

The current machine's canonical port is occupied by the live local service; the
synthetic board preview runs on an independent in-memory fixture port. The
separate live-data preview uses authenticated reads from the installed API,
without database access or migrations. No production
identity, grants, native installation or data were changed for qualification.
A fixture bridge or successful WASM test must never be reported as this gate.
A disposable VM is not configured for the current integration task, so installed
qualification is blocked. Also cover independent archive/restore, all six Todo
kinds, proposal decisions, locked Wallet, worker restart, changed/revoked grants,
wrong caller, denied read-credential writes, actual persistence migrations and
rollback, and SDK read credential disposal. Recovery requires fresh approval and
status only; missing receipts stay unknown. Preserve Wallet custody, registration,
membership, Files and generic-presentation regressions.

Full legacy removal is a separate dependent change after replacement qualification.
Repeat installed acceptance after removal using final package hashes; an earlier
pass is insufficient. Until the installed test passes, keep browser-editing release
claims unqualified. Read-only preview is independently usable.

## Recorded source checks

The public implementation at `30d0464` passed `bash docs/dev/verification.md`
(exit 0: 2,310 passed, 5 skipped) and `ruff check src tests scripts integrations`.
Shared Rust native/all-feature and WASM checks passed. Wallet's complete npm test
suite, TypeScript checks and Rust workspace suite passed at `d6f2b55`; secS workspace
test/build passed at `306f713`. The matching private SDK passed its Rust/native
suite, WASM build, JavaScript tests, TypeScript checks and integrated Python
verification (2,267 passed, 5 skipped). Disposable socket tests require loopback
permission. Opt-in live database tests were not run.

Manual in-app-browser checks confirmed direct-child vs descendant scope, persisted
filters, all four themes, 390px column selection, right-click reading, supporting
material and focus restoration. A 1,500-item disposable board rendered 30 initial
cards and 60 after one incremental load, retaining the complete 1,500 count.
Pagination focus was subsequently corrected and rechecked. These checks are UI
and source-contract evidence, not the installed Chrome signing gate above.
