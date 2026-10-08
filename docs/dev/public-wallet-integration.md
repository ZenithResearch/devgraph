# Public Devgraph Wallet integration

All active Devgraph implementation belongs in `ZenithResearch/devgraph` PR #10.
`devgraph-private-history-20260915` is a deprecated historical archive. Devgraph-owned
SDK, generic authority, delegated read and guarded receiver files were transferred
from archive commit `331331a312727489e58579ffd3de13abe11efeb1` without importing its
Git history. Historical planning/workstation inventories were not copied.

The public branch's current UI, compact daily briefing, project filters, license,
Python dependency fixes, agent packages and canonical protocol remain in place.
The root Rust workspace builds the canonical protocol/core/WASM from one public
revision. Native compatibility crates have independent lockfiles so the private
Wallet dependency does not prevent public verification.

Wallet remains application-neutral and separately maintained. Current merged
Wallet is pinned at `ff8de6ddc04ac2fb6b39408274276bc4ce984d42`; secS Todo authority
still depends on #298 and its #297 base. See [source pins](generic-wallet-source-set.json).
Read credentials stay outside Wallet and never authorize mutations. Unknown writes
retain their request/key and recover by freshly approved status lookup only.

Qualification candidates/reports now use v2 and require exactly three revisions:
`devgraph_public`, `wallet`, `secs`. Old four-source reports are historical evidence
and cannot qualify a new public artifact. The SDK build manifest is v2 and binds
the local canonical protocol to the same public commit.

Run repository/Python/frontend checks, root Rust/WASM, the separately scoped native
checks, SDK/browser fixtures and packed-consumer checks. Record exact source and
package hashes after committing. These checks establish source compatibility only.
Installed Devgraph → Wallet → secS → receiver qualification remains blocked without
a disposable macOS VM. Legacy retirement requires that pass and a second pass on
final removal artifacts. Live activation is outside this delivery.
