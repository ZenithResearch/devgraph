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


### Kanban result admission and decoder installation

Kanban is a projection and editor of Devgraph; it does not own a lifecycle or
approval policy. The receiver enforces requirements, evidence, authorization,
versions and completion. Kanban and the web SDK admit results using the same
Rust/WASM `PreparedMutation` decoder and refresh the board from the read API
only after a validated commit. Malformed results are never success.

Build the matched `@devgraph/web` package (`npm ci --ignore-scripts` then
`npm run build` in `packages/web`). Set `DEVGRAPH_WEB_SDK_DIR` to the absolute
package directory containing `sdk-build.json` and `dist/` when starting the
receiver. The server checks the manifest hashes and serves only an immutable
snapshot of the decoder factory and WASM. Python-only installations remain
readable; missing decoder assets disable editing with a setup message. This
configuration change is not a trust installation or an authority grant.

Only a recognized, strictly decoded rejection of an initial execute request
clears its pending state. A refused or missing status lookup, malformed result,
proxy error, or lost response cannot establish the original outcome. Recovery
keeps the original request and idempotency key, obtains fresh Wallet approval,
and checks status without resubmitting the mutation. Historical signed request
bytes and receipt formats are unchanged.

All tracked Rust lockfiles are audited by `python3 scripts/audit-rust-locks.py`;
this includes standalone native and retained browser-host workspaces. A new
lockfile enters the CI gate automatically. Dependency changes require new
package evidence; previous Wallet Chrome acceptance cannot qualify new bytes.

## Public Chrome extension

The public organization browser package is tracked separately as
`wallet_browser_public` in `generic-wallet-source-set.json`. It adds the generic
`readAccess` bridge, `access_request_v1` and `graph_access_v2` to the existing
provider-profile, connection and generic write-presentation contracts. Do not
install the historical personal-repository development package as a fallback.
Chrome 0.0.2 also adds `read_setup_status_v1`: connected sites can reuse an exact
active provider profile without repeating setup review. Locked or unconnected
Wallets disclose no stored trust; changed profiles still require review. Read
consent and server grant checks remain mandatory.
See [the public Wallet contract and update instructions](https://github.com/ZenithResearch/castalia-wallet/blob/7d81c5b0a9f74271fb5b2d5ea8d2711df40bf485/docs/browser-graph-access.md).

Use `node scripts/qualification/wallet_provider_contract.mjs /path/to/public-wallet --browser`
to check the pinned public parser and this receiver's profile fixture. The
existing native/read-authority source roles retain their own historical pins;
this additive browser role does not silently repin their frozen dependencies.
The exact public package was also loaded in disposable Chrome with this
DevGraph client's connect/request/read flow and a synthetic issuer/receiver.
Real Wallet trust, consent and member-grant decisions remain separate checks.
This browser bridge preserves existing signed bytes; it does not implement the
separate canonical-service credential/store migration.
