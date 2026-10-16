# SDK implementation and qualification

The Devgraph SDK, application transport and guarded receiver are maintained in
public `ZenithResearch/devgraph`, in PR #10. The private history repository is a
deprecated archive. Its Git history is not an implementation dependency.

The SDK uses this checkout's canonical `devgraph-work-protocol` crate. Python,
native Rust and WASM retain the historical request/signature vectors and canonical
Todo v2 contract. External companion pins are recorded in
[the source set](../dev/generic-wallet-source-set.json). Wallet stays in its own
repository; no Wallet source is vendored here.

## Reproduction

Use Rust 1.96.0, Node 24.18.0 or newer and wasm-bindgen 0.2.127. The public root
workspace contains the protocol, core SDK and WASM. It needs no private Git source.

```sh
cargo test --locked --workspace --all-features
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
cargo check --locked -p devgraph-web --target wasm32-unknown-unknown
cd packages/web
npm ci --ignore-scripts
npm run build
npm test
npm run test:browser
npm run build
npm pack --ignore-scripts
```

Native crates have separate workspaces and lockfiles because their companion
contracts require authorized Wallet source access. Each manifest patches the
companion protocol dependency to this same canonical public crate, keeping one
Rust request type without changing signed bytes:


```sh
cargo test --locked --manifest-path crates/devgraph-client-native/Cargo.toml
cargo test --locked --manifest-path crates/devgraph-browser-host/Cargo.toml
```

The browser host is retained compatibility source pending the separate retirement
gate; merged Wallet has removed its old bridge. New integrations use generic
credential presentation. The SDK 0.2 preview reads through Devgraph with explicit
in-memory browser credentials, and requires separate provider approval and Wallet
connection clicks for writes. See [migration](../dev/kanban-generic-wallet-migration.md).

`sdk-build.json` v2 records the exact public source revision, dirty status, local
protocol source, dependency closure, tool versions and artifact hashes. It replaces
the old assumption that the SDK lives outside the canonical protocol repository.
An npm `private: true` flag prevents accidental registry publication during preview;
it does not mean the Devgraph source repository is private.

Source/browser fixture success does not establish installed authorization. The
[disposable macOS gate](../dev/generic-wallet-qualification.md) remains blocked until
an isolated VM is available. No live credentials, grants, trust or installation
are changed by this source integration.
