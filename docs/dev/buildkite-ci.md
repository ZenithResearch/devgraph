# Devgraph Buildkite history and manual qualification

Public PR/main checks have moved to [GitHub Actions](github-actions-ci.md).
The public `devgraph` Buildkite pipeline is retired; its configuration remains
for historical reproducibility. The manual companion/installed pipelines below
remain separate qualification tools and are not required public source checks.

## Historical configuration

The previous Buildkite configuration owned repository, Python/package, frontend, Rust/protocol/MSRV, WASM,
SDK/browser, CodeQL, companion source and installed-evidence admission jobs.
Repository tests run as an unprivileged user so Linux root cannot bypass permission tests.
GitHub remains the source/review host. These pipelines never deploy Devgraph,
install live signing binaries, provision grants, or approve Wallet requests.

## Three pipelines, three different claims

| Pipeline | Primary repository | Configuration | Meaning of a pass |
| --- | --- | --- | --- |
| `devgraph` | `ZenithResearch/devgraph` | `.buildkite/pipeline.yml` | Public source, packages and security analysis |
| `devgraph-companion` | `bananawalnut/castalia-wallet` | `.buildkite/companion-pipeline.yml` from reviewed Devgraph commit | Exact matched source checks on macOS |
| `devgraph-installed` | `ZenithResearch/devgraph` | `.buildkite/installed-pipeline.yml` | Admission of independently produced disposable-macOS evidence |

Keep pipeline visibility private. Public checks need no private repository access
or custom secrets. Use the existing Buildkite GitHub repository providers and
hosted queues (`linux-small`, `linux-medium`, `macos-15-medium`). Queue names are
CI infrastructure settings, not product network configuration. Configure public
push/main and PR-to-main triggers, with fork builds disabled until isolated
untrusted-build policy is reviewed. Companion and installed lanes are manual-only.
The macOS source queue does not automatically qualify the installed VM gate.

The public pipeline's initial command is:

```sh
buildkite-agent pipeline upload --no-interpolation .buildkite/pipeline.yml
```

Before the new configuration is on main, create a build for its full reviewed
commit and branch. Do not mistake an old main checkout for the migration candidate.

## Private Wallet access without another App

The companion pipeline checks out Wallet through its existing Buildkite GitHub
connection. This is the only private source repository needed. Devgraph and secS
are public. No `SDK_SOURCE_APP_CLIENT_ID`, `SDK_SOURCE_APP_PRIVATE_KEY`, Wallet
recovery key, broad personal token, or Actions secret is needed.

The SDK is Devgraph's client library; it is not a GitHub App. The retired Actions
setup proposed a separate machine identity merely to fetch private Wallet source.
Do not create that identity for this Buildkite configuration.

In the companion pipeline settings, set `DEVGRAPH_REVISION` to the **reviewed full
Devgraph commit**. Start the build at the exact `wallet_generic.revision` in that
commit's `docs/dev/generic-wallet-source-set.json`. Use this initial command:

```sh
set -euo pipefail
[[ "$DEVGRAPH_REVISION" =~ ^[0-9a-f]{40}$ ]]
git init build/devgraph
git -C build/devgraph remote add origin https://github.com/ZenithResearch/devgraph.git
git -C build/devgraph fetch --depth=1 origin "$DEVGRAPH_REVISION"
git -C build/devgraph checkout --detach FETCH_HEAD
test "$(git -C build/devgraph rev-parse HEAD)" = "$DEVGRAPH_REVISION"
cd build/devgraph
buildkite-agent pipeline upload --no-interpolation .buildkite/companion-pipeline.yml
```

Restrict editing/starting/unblocking this pipeline to the existing trusted
maintainers. Review both SHAs before the source-review block: unreviewed public
code must never execute beside private Wallet source. A separate job fetches the
same immutable Devgraph revision; its preflight checks both actual checkouts.
The generic source checks use fresh private build caches and publish only named
summary artifacts. A missing source or failed check fails the job, never skips it.
No source bundle containing private Wallet code is uploaded.

## Installed qualification

The installed pipeline uses the same initial upload command with
`.buildkite/installed-pipeline.yml`. The operator selects a precise Buildkite build
UUID and job UUID containing `qualification.tar.gz`, with `candidate/` and
`evidence/` produced by the actual [guest harness](generic-wallet-qualification.md).
Admission rejects unsafe archive paths, wrong source revisions, changed artifact
hashes, missing scenarios and synthetic evidence. It publishes only admission
metadata. A manual unblock alone cannot make invalid evidence pass.

## Security analysis and cutover

SDK and companion jobs audit all npm dependencies, including development tools,
and audit the separately installed SDK consumer before running it. Findings at
low severity or above, or an unavailable audit service, fail the lane. Both JSON
reports are retained with its provenance.

CodeQL CLI and queries come from a checksum-pinned official bundle. Python and
JavaScript SARIF reports are retained in Buildkite; inspect findings as part of
review. GitHub's separately configured default CodeQL scan is outside repository
YAML. Leave that scan and current required checks enabled until the new Buildkite
checks pass and their equivalent check requirements/reporting are accepted.
Uploading SARIF into GitHub needs a separately authorized reporting permission;
ordinary source checkout access is not that permission. No token is requested
implicitly and no protection is disabled by these scripts.

Buildkite [build #2](https://buildkite.com/bananawalnut/devgraph/builds/2) passed
all four public lanes at `4489e0be2df382daabbff38f12d331e43b3cbdf3`. The three
Actions workflow files are retired in this candidate. The former migration required `buildkite/devgraph`
with strict up-to-date checking before removing the former `Repository verification`
requirement; never leave a merge window with neither set required. Preserve historical Actions results as
evidence. CodeQL Actions-language analysis becomes inapplicable once executable
Actions workflows are retired; Python/JavaScript scanning remains.

Pinned tools: Python 3.10 via uv 0.11.6, Node 24.18.0, Rust 1.96.0, MSRV 1.85.1,
wasm-bindgen 0.2.127, wasm-pack 0.14.0, cargo-audit 0.22.1, CodeQL 2.27.2.
Each lane records its source commit, pipeline-file hashes, tools and Buildkite
job/build IDs. Source, companion, installed qualification and live activation
remain separately reported.
