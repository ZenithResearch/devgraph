# DevGraph GitHub Actions CI

`.github/workflows/source.yml` runs the existing four public verification lanes
on disposable Ubuntu runners for PRs to `main`, pushes to `main`, merge groups
and manual dispatch. The portable scripts live under `scripts/ci`; old Buildkite
entry points forward to them for historical/manual qualification compatibility.

| Lane | Checks |
| --- | --- |
| repository | Locked Python environment, repository tests/generated contracts, frontend tests, strict lint, wheel/sdist and source/agent bundles |
| protocol | Rust request vectors, hostile representations, WASM compilation, Rust 1.85.1 minimum and formatting |
| sdk | Rust tests/clippy, strict Rust/npm audits, WASM, SDK tests, Chromium/Firefox/WebKit and installed-consumer checks |
| codeql | Checksum-pinned Python/JavaScript CodeQL bundle; retained SARIF reports |

The required check is **DevGraph CI**, produced by the GitHub Actions app.
It runs with `always()` after the entire matrix and succeeds only when every
lane succeeds. Failure, cancellation or a skipped lane blocks the gate. There
are no path filters or continue-on-error exemptions. Default GitHub CodeQL
scanning remains independent and enabled.

Public jobs need no custom secrets or private source. Checkout has read-only
permissions and does not persist credentials. PRs use their merge checkout;
branch protection retains strict up-to-date checks, administrator enforcement,
and the existing force-push/deletion restrictions. Replace the Buildkite required
check only after this exact workflow's complete gate passes. Never leave a gap
with neither check required.

Checks use the same pinned tool versions and commands as the prior public
pipeline. Each lane records source SHA, script hashes, tool versions and Actions
run metadata. Downloaded tools/temporary consumers are removed on exit. Hosted
runner disks are disposable; no local build caches or device installations are
created. Named evidence/package artifacts expire after seven days. Superseded
runs are canceled to avoid accumulating redundant work.

After cutover, pause the old public Buildkite pipeline and disable its automatic
PR/push triggers. Keep historical results and the manual companion and installed
evidence-admission configurations. These manual checks are not implied by a
public CI pass. Nothing in this workflow deploys DevGraph, starts a database,
installs signing components, approves requests or changes grants.
