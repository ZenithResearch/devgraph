# Dependent legacy retirement inventory

Status: **Devgraph/secS removal gated; Wallet-owned removal merged in #31/#28**. The replacement source must
first pass the disposable macOS installed gate. Prepare removal as a separate PR
based on that qualified Devgraph revision; do not merge it using a fixture-only pass.

| Owner | Remove after consumers qualify | Preserve |
|---|---|---|
| Wallet (merged, [#31](https://github.com/bananawalnut/castalia-wallet/pull/31)) | Removed: `provider.devgraph`, Devgraph-specific consent/routes, Devgraph browser bridge and native-messaging/package registration, old Issue/Work signing binaries | Generic credential signing, custody/recovery, membership, registration and Files; historical signing decoders/vectors |
| Public Devgraph | `devgraph-browser-host` compatibility crate/install wiring; web `connectCastalia`, old connection/read-grant coordinator and fixture transport; active legacy terminal adapters and old preview packaging | Generic SDK reads and approval path; historical authority/result decoders, receipts, exact request/signature fixtures and data migrations |
| secS | Active Issue-create and Work v1 signer/Wallet adapters and their installed CLI entrypoints | Shared policy/admin lifecycle needed by v2; historical projection/replay decoding and immutable signature data |
| Documentation/CI | Old bridge as target architecture, legacy extension/native-host build gate | Exact-artifact generic gate, source checks, historical compatibility evidence |

Move policy/admin functionality still required by v2 before removing its containing
binary. Missing generic trust, versions or authority must produce setup/upgrade
errors, never fallback. Native Devgraph reads keep owner-private access; browser
reads take explicitly supplied in-memory credentials.

New package hashes invalidate the earlier installed pass. Rerun browser and native
qualification against final removal artifacts before release. Do not alter live
registration, grants or installations as part of preparing these PRs.

The merged Wallet release contains no old Issue/Work signing binaries or Devgraph
browser adapter. Devgraph's `castalia-wallet-presentation` pin now points at
`ff8de6ddc04ac2fb6b39408274276bc4ce984d42`. Its remaining
`castalia-wallet-devgraph-presentation` pin at `3216144e8df04582e921810462845efc8c2cf390`
is a frozen compatibility dependency for the gated old host/client code. It must
not be repinned to merged Wallet (the crate is removed there), packaged as the new
Wallet, or used as a fallback. Legacy beta installers describe historical releases,
not the replacement generic candidate.
