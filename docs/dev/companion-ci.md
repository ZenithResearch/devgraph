# Matched companion source checks

The public SDK job has no private-source credential. The companion job is a
trusted, manually dispatched macOS source check. It does not qualify installed
Wallet approval, create grants, install trust, or activate a live receiver.

## Source-access setup

Reuse an operator-approved GitHub App installed on **only**
`bananawalnut/castalia-wallet`, with repository **Contents: read-only** permission.
In `ZenithResearch/devgraph` Actions settings (or an approved organization scope
restricted to that repository), supply:

| Setting | Kind | Value |
| --- | --- | --- |
| `SDK_SOURCE_APP_CLIENT_ID` | Actions variable | Approved source App client ID |
| `SDK_SOURCE_APP_PRIVATE_KEY` | Actions secret | That App's private key |

Do not create a broad personal token, expand an existing installation, put keys
in Git, or paste them into logs/chat. An operator must approve any new App/access
before provisioning. An absent setting fails before token issuance with this
setup reference. Presence alone is not evidence the installation is authorized.

Dispatch only a reviewed branch in the trusted repository. Do not convert this
lane to `pull_request_target`, execute fork code with credentials, or add source
tokens to ordinary PR jobs. The token action requests only Wallet contents read;
the Git helper serves it only for that exact HTTPS GitHub repository and only
during fetch. Git remotes and artifacts never contain the token. Tests run
without it. secS source is public and is fetched separately at its exact pin.

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
workflow. Required source tests cannot be ignored/skipped. The manifest records
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
