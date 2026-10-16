# Matched companion source checks

The public SDK job has no private-source credential. The companion job is a
trusted, manually dispatched macOS source check. It does not qualify installed
Wallet approval, create grants, install trust, or activate a live receiver.

## Source access — use the existing Buildkite GitHub connection

No custom GitHub App, SDK App private key, Wallet key or Actions secret is
required. The private companion pipeline connects to `bananawalnut/castalia-wallet`
as its primary repository, using Buildkite's existing GitHub repository provider.
The public Devgraph and secS sources are fetched over HTTPS at exact commits.
The name “SDK” describes the client library under test, not an application to create.

Use `.buildkite/companion-pipeline.yml` and the reviewed bootstrap described in
[Buildkite CI](buildkite-ci.md). Disable automatic branch/PR triggers for this
private lane. A maintainer reviews the immutable Devgraph revision before
unblocking. The preflight checks both the Wallet pipeline commit and actual
checkout against Devgraph's source-set pin, rejects PR-triggered builds and dirty
sources, and never treats a missing checkout as a skipped success.

Buildkite already fetches Wallet for this job. Historical private Cargo revisions
are fetched into that same Git database, then Cargo resolves them locally through
a temporary Git configuration. No private key is copied, no token is embedded in
URLs, and no source-access secret is written to artifacts. Tests use the offline
fetch cache. Do not export private Wallet source or raw fixture state as artifacts.

The old `SDK_SOURCE_APP_CLIENT_ID` / `SDK_SOURCE_APP_PRIVATE_KEY` proposal was
specific to the retired Actions workflow. Do not configure those settings.
This does not authorize broadening any Buildkite repository installation.

## Scope and evidence

`docs/dev/generic-wallet-source-set.json` pins both companion heads. The runner
rejects dirty/wrong companion checkouts and checks the read crate's retained
provenance hashes against the paired Wallet. It runs:

- Independent Rust Wallet fixture generation, presentation and read-core tests.
- secS Work/Arena/Todo signatures, grants, policy/replay checks and native-v2 CLI.
  The independent Wallet fixture is required and its named test must execute.
- Fresh secS Work/Todo vector generation and equality with the vectors consumed
  by Devgraph's verifier tests.
- The standalone read authority and vendored contract, including the actual
  source-built binary in Python native enrollment/read tests.
- Rust custody/recovery driver → enrollment → secS → guarded read receiver using
  disposable identities, and the pinned Wallet provider parser.

The existing native SDK, WASM, browser and external package checks remain in the
pipeline. Required source tests cannot be ignored/skipped. The manifest records
source and runner hashes, tools, executed test counts, fixture hashes and native
test-binary hashes. A failure replaces any previous passing manifest. Uploads
are restricted to the named summaries and package outputs, never temporary
fixture/credential stores or arbitrary build directories.

To reproduce, fetch locked dependencies for both companions and the separate
`tools/read-authority` manifest, then use clean checkouts at the source-set pins:

```sh
uv sync --locked
uv run python scripts/sdk/companion.py --wallet /path/to/paired-wallet \
  --secs /path/to/paired-secs --build-root /path/to/disposable-build-cache
```

The runner uses offline Cargo after fetch. Local Devgraph modifications are
explicitly recorded as `devgraph_dirty`; only a clean pushed head and its remote
checks can support the merge packet. Read `companion-source.json` in the CI
artifact for the actual result. Installed Chrome/native approval qualification
remains a separate exact-artifact gate.

Keep the build cache exclusive to these paired checkouts. Reusing a Cargo target
directory between different source worktrees can retain the older test binary.
Use separate build roots, or clean the affected source packages before switching
checkouts. The runner requires the named workflow/Todo grant and rejection tests
as well as fresh vectors; a foundation-only test binary cannot certify the Todo
candidate.
