# Native companions for the signed beta

Devgraph's signed Work and Arena commands need the native Castalia Wallet signer
and secS producer. The beta Python package and agent integrations do not replace
these programs. This runbook packages the exact reviewed sources and reconstructs
their binaries without copying any operator identity, authority, configuration,
database, browser profile or Git history.

The initial target is **Apple Silicon macOS**. Package versions are both `0.1.0`;
use the complete source revision and binary digest to identify the Arena build.

| Companion | Reviewed source revision | Cargo package / binary |
| --- | --- | --- |
| [Castalia Wallet](https://github.com/bananawalnut/castalia-wallet) | `96a336747d5b050d334ec565b3c06de3b6b23f38` | `castalia-wallet-devgraph-cli` / `castalia-wallet-devgraph-work-v1` |
| [secS](https://github.com/ZenithResearch/secS-magik) | `2540d1ae37cac9c6d439bcd49aee0f94c4279889` | `server` / `secs-devgraph-work-v1` |

Both branches contain their earlier named Work and native grant-administration
prerequisites. Do not substitute either repository's older main branch. A
September 14, 2026 read-only remote check found neither revision on GitHub, no
Arena pull request and no GitHub release in either companion repository. Source
bundles make the reviewed code transportable without a dependency on unpublished
remote commits. Publishing or merging those branches is a separate action.

## Redistribution status

These are **private review artifacts pending the owner's licensing decision**.
The exact Wallet source has no repository license file and its native CLI crate
declares no license. The separate `castalia-wallet-core` crate's MIT declaration
does not establish permission for the CLI or the rest of the Wallet source tree.
Do not relabel that archive or binary using Devgraph's chosen license.

The exact secS archive contains its MIT `LICENSE`, copyright 2026 Zenith
Research; retain that notice with redistributed source and binaries. Both
binaries also contain registry dependencies. Review their applicable licenses
and distribute the required notices before publishing compiled artifacts. This
script does not generate a complete compiled-dependency notice inventory, grant
distribution rights, sign/notarize software, publish packages or change licenses.

Generating these artifacts is not a claim that they are cleared for public
redistribution. Resolve Wallet's source **and** binary terms before attaching the
companion bundle to a public beta release. An upstream licensing update creates
a new source revision; re-review and update the pins rather than silently
rewriting these archives.

## Prepare the bundle

Run this from the Devgraph source checkout, supplying two clean checkouts whose
HEAD revisions exactly match the table. The output must be new and outside all
source checkouts:

```sh
python3 scripts/build_beta_native.py \
  --wallet-source /path/to/castalia-wallet-arena \
  --secs-source /path/to/secs-arena \
  --output-dir /path/to/staging/native-beta
```

The script reads only committed files through `git archive`. It rejects dirty
or incorrectly pinned source, submodules, links, unsafe paths and common private
file locations. Exported file inventory, Git blob hashes and executable modes
must match the pinned tree; export attributes cannot silently omit or transform
source. Source archives normalize timestamps and ownership, retain
executable modes, and have deterministic SHA-256 digests. Tracked synthetic test
fixtures remain source material; no live runtime directory is traversed.

Output includes both source archives, `native-manifest.json`, `SHA256SUMS`, a
standalone copy of the build helper, and `BUILD.md`. The manifest records source
and optional binary hashes, fixed install paths, platform and licensing status.

Optional `--wallet-binary /path/to/candidate --secs-binary /path/to/candidate`
includes **both** previously reviewed arm64 binaries after verifying these exact
digests. Omit those arguments for a source-only bundle:

| Binary | SHA-256 |
| --- | --- |
| Wallet | `42187a4ddd8343b3f0ac405a97ccb5964d91ef2eca1ebd3eebb394f045601b40` |
| secS | `ad93c73315dcf03e93da22c0f02c7a225d46d81312c1e42e10a0606d6e3374fc` |

Those hashes identify the reviewed local candidates, not a promised hash for
every rebuild. No credential or operator configuration is part of this manifest.

## Build the archived source on another machine

Install Python 3.10+, Rust/Cargo 1.96.0 and Apple's Xcode Command Line Tools.
Each archive contains its complete Cargo workspace and lockfile. Wallet's native
CLI has no dependency on its browser extension or core crate. secS depends on
the `core` and `permissions` crates in its own archive. Neither lockfile has Git
dependencies; there are no external path dependencies or submodules to bundle.
Registry packages are not vendored and must be obtained using Cargo's locked
registry checksums. Rust and the platform SDK are external build inputs.

Inside the bundle:

```sh
shasum -a 256 -c SHA256SUMS
python3 build_beta_native.py --bundle-dir . --verify-build
```

Use `--offline` when all required registry packages are already cached. The
helper extracts the checked archives and uses a fresh `verification/target`
directory. It executes only these native build recipes:

```sh
cargo build --locked --release -p castalia-wallet-devgraph-cli --bin castalia-wallet-devgraph-work-v1
cargo build --locked --release -p server --bin secs-devgraph-work-v1
```

Both builds use `CARGO_INCREMENTAL=0`. Only tool/cache discovery environment is
forwarded; signer, proxy and compiler-wrapper overrides are excluded. The
selected `rustc` is passed explicitly to Cargo. Successful verification writes
`verification/build-report.json`, compiler versions, actual binary digests and
whether each matches the known candidate. Logs remain under `verification/`.
Source, compiler, linker, macOS SDK and build paths can affect machine-code bytes;
deterministic source archives do not establish bit-for-bit binary reproducibility.
Verification builds do not execute the resulting signers or contact Devgraph.
The directory must not already contain `verification/`; preserve prior evidence
and use a fresh bundle directory for another independent build.

Measured validation on September 14, 2026: both archives rebuilt successfully
twice with Rust/Cargo 1.96.0, offline registry caches and empty build target
directories on Apple Silicon macOS. The first run inherited the shell build
environment and matched both candidate SHA-256 digests above exactly. The final
helper's restricted-environment run also succeeded, producing different hashes:

| Restricted-environment rebuild | SHA-256 |
| --- | --- |
| Wallet | `48bda593b4c2118aaf13dc278973a6d943b239a34f014777326bbc5b431b9bd7` |
| secS | `d2e353743a2a0e941566104c47465ff22fec9061735a2703e6d4bd137e4d63bc` |

The cause of that binary difference has not been established. Both runs prove
archive build completeness; neither is a universal reproducibility guarantee.
Source archive bytes are identical across the two packaging runs. Optional
binary artifacts in the final bundle remain the exact reviewed candidates, not
these later rebuilds. No original checkout or installed native program was used
as a compilation input. All seven delivered artifact checksums, 15 focused
packaging tests and Ruff checks passed. Keep the manifest and actual build report
with any candidate that proceeds to runtime qualification.

## Install layout and machine enrollment

After verifying the selected artifact and resolving its distribution terms,
place the executables at these paths relative to the **operating-system account
home**, which Devgraph resolves through the account database:

```text
Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-work-v1
Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1
```

Use owner-controlled, non-symlink directories and executable regular files.
For a new machine, one explicit installation recipe is:

```sh
account_home="$(python3 -c 'import os,pwd; print(pwd.getpwuid(os.geteuid()).pw_dir)')"
install -d -m 700 "$account_home/Library/Application Support/Zenith/CastaliaWallet/bin"
install -d -m 700 "$account_home/Library/Application Support/Zenith/secS/bin"
install -m 700 verification/target/release/castalia-wallet-devgraph-work-v1 \
  "$account_home/Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-work-v1"
install -m 700 verification/target/release/secs-devgraph-work-v1 \
  "$account_home/Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1"
```

The packager does not execute these installation commands. Do not use the recipe
to overwrite an existing installation without the normal update procedure.
Install layout alone grants no authority. Complete the documented local host and
signer setup, migration 26, receiver configuration and explicit Work/Arena grant
provisioning for the new operator. Arena writes require the 48-rule profile;
ordinary renewal preserves an existing 41-rule Work-only profile. Use the
Devgraph plan/apply workflow and validate signed requests and denial behavior.
Never distribute the maintainer's seed, issuer keys, trust files or grants as
clean-machine setup material. See [named Work](../named-work-v1.md) and the
[Arena contract](../../ontology/arena-runtime.md).
