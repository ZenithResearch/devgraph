# Installed qualification tools

These two executables are **qualification tooling, not supported application
signers or installers**. They can build on a development Mac, but refuse to run
operations outside a manual Buildkite macOS `VirtualMac` guest initialized by
`macos_gate.py init-guest`. They derive the actual OS account home through secS;
changing `HOME` or setting Buildkite variables on a physical Mac is insufficient.

- `qualification-authority-admin` exposes the pinned secS shared
  `devgraph_work_admin::AdminCli` only. It can provision an explicitly selected
  disposable policy, inspect it and snapshot/verify that authority. It does not
  expose the retired Work/Issue signer, generate Wallet keys, approve Wallet
  trust, or install the separate v2 credential configuration. Do not install it
  at an old signer path. Policy provisioning remains an explicit operator action.
- `qualification-native-client` consumes the public native SDK and portable Rust
  result decoder. It reads owner-private request/idempotency/profile references,
  invokes the fixed generic Wallet with its real terminal approval, and records
  success only after validating the committed result and request/receipt binding.
  Ctrl-C cancels through the SDK's dispatch boundary. A lost, malformed or
  unretained response after dispatch remains `unknown`. There is no mutation
  retry. `--reconcile` asks for fresh approval and uses the original status-only
  route, request bytes and idempotency key. Keep those input files intact.

## Build from the reviewed runtime tuple

`build.py` pins the runtime Devgraph and secS revisions explicitly. Supply **clean
Git checkouts** at those exact revisions. The helper source has its own reviewed
revision/hashes; it does not relabel the runtime wheel or SDK source. Update and
review the pins deliberately for a different runtime candidate.

```sh
python3 scripts/qualification/native-tools/build.py \
  --devgraph /absolute/clean-devgraph-candidate \
  --secs /absolute/clean-secs-candidate \
  --output /absolute/new-private-tool-build
```

The builder renders a machine-local Cargo manifest from `Cargo.toml.in` and uses
the frozen lockfile. It runs tests and Clippy before building either release
executable, rechecks both source checkouts, and publishes only the two binaries
under `artifacts/` with `tool-provenance.json` beside them. It never provisions
authority or invokes Wallet. `--offline` is available after dependency fetch.

Wallet's current and historical decoder crates remain private dependencies. In
the existing Wallet-connected Buildkite lane, use its established read-only local
Git rewrite after fetching the reviewed Wallet revisions; no new GitHub App,
private key, token scope or production credential is needed. Do not put repository
credentials in a manifest or provenance file. The historical decoder is a frozen
compatibility library, not permission to revive an obsolete executable.

Audit the tracked lockfile using the repository's Rust audit command. The pinned
secS library includes the already documented informational bincode maintenance
warning; this does not imply a new vulnerability or authorize changing historical
bincode request formats.

## Package and observe

Declare both executable paths and SHA-256 values under an additional
`qualification_tools` role in the candidate manifest. Keep build provenance beside
the candidate. Run the unchanged inventory validator, compare the existing
runtime roles against their accepted hashes, then install/run tools only in the
disposable guest. Any added or changed binary creates a **new candidate hash** and
requires a fresh installed run; no earlier case pass carries forward.

`qualification-native-client --help` lists input references. Its output file must
be new and owner-private. It preserves the original request/idempotency files;
an existing result is never overwritten. Public receipts from disposable data
may enter evidence after review. Credentials, custody files, policy files and
authority snapshots must not be uploaded. The application SDK still enforces
fixed native paths and current profile/trust/resource/version admission.

Source tests, helper refusal tests and a healthy receiver do **not** satisfy any
complete installed scenario. The full matrix, real human consent, historical
store rollback and independent evidence admission remain required by
[the release gate](../../../docs/dev/generic-wallet-qualification.md). After legacy
retirement, rebuild the final artifacts and repeat the complete run.
