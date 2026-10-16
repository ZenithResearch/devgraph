# Coordinated upgrade: source checkpoints

15 October 2026. Source implementation and disposable fixture evidence only.
These checkpoints do not qualify or activate the installed mutation chain.

## Buildkite migration — current checkpoint

The current source-set pins are Wallet #32 `6c9c2e5` and secS #298 `254f744`,
including the secS #297 foundation `d8148f8`. Full revisions are recorded in
`generic-wallet-source-set.json`. Earlier sections below are historical checkpoints.

Buildkite [Devgraph #2](https://buildkite.com/bananawalnut/devgraph/builds/2)
passed repository/package, protocol/MSRV, SDK/WASM/browser and CodeQL checks at
`4489e0be2df382daabbff38f12d331e43b3cbdf3`. The first migration run exposed root
bypassing a filesystem-denial fixture; the repository lane now runs unprivileged,
without weakening or skipping that test. Local verification passed 2,694 tests
with 10 opt-in skips, and Ruff including integrations passed. Removed test cases
belonged to the retired custom GitHub App credential helper.

Remote companion tests now use the existing Buildkite Wallet repository
connection. No new source App, App private key or Actions secret is needed.
See [Buildkite setup and separation](buildkite-ci.md) and
[companion checks](companion-ci.md). Source checks remain distinct from installed
qualification, which still needs actual disposable macOS evidence.

## Candidate HTTP/process dependency floor

The candidate previously locked AnyIO 4.14.1, affected by GitHub advisories
`GHSA-82r6-8w77-94w6` (TLS hostname validation), `GHSA-3w57-8xmc-8v26`
(subprocess supplementary groups) and `GHSA-5p39-cfhj-2xmp` (worker stderr
blocking). The lockfile now selects the published 4.14.2 fixes. Package metadata
also requires `anyio>=4.14.2,<5`, so an external wheel installation cannot resolve
the older vulnerable dependency. No other dependency version changed. This
updates only the source candidate and its isolated test environment; live service
dependency activation remains separate. Repository verification under AnyIO
4.14.2 passed 2,699 tests with the same 10 opt-in skips and passed generated-file
and static checks.

## Installed native inventory follow-up

Qualification report v3 now binds the actual fixed-path Wallet/secS mutation
binaries to the candidate. The guest checks hashes, ownership, symlinks and
POSIX permissions before beginning, recording or finishing a run. `finish`
also rechecks evidence-file hashes; a later observation invalidates completion.
Offline CI requires both installed inventories and rejects old v2 reports.
Candidate v2 and application/signing contracts are unchanged. The nine admission
tests use disposable files and a mocked guest; they are not installed signing
qualification. Package, SDK, extension and standalone-read installed-file checks
remain mandatory operator evidence. See [the gate guide](generic-wallet-qualification.md).
Repository verification after this change passed **2,699 tests with 10 existing
opt-in skips**; generated artifact checks, Ruff (including integrations), formatting
of the changed Python files and whitespace checks passed. The prior matched
companion run below belongs to `c2d5b52`; its runtime pins and contracts are unchanged.

## Companion CI and review follow-up

That earlier candidate pinned Wallet #32 `f654017` and secS #298 `a1b09ab`, which
includes the #297 foundation at `d8148f8`. The source-set manifest is authoritative
for full revisions. The secS changes add stacked-branch CI, a standalone native
read lane and five actual-process tests. A method-scoped allowance handles an
`async_trait`-generated Rust 1.99 lint; no handler or protocol behavior changes.
Both stacked branches contain that fix so each foundation can pass independently.

The Devgraph companion runner now requires an independently generated Wallet
fixture, exact source pins, fresh Work/Todo vectors and native read tests. It
rejects absent tests and skipped required cases. Its credential helper only
serves the approved private Wallet repository during fetch; preflight fails
before issuance when the source App is not configured. See
[companion CI setup and evidence](companion-ci.md).

| Follow-up check | Result |
| --- | --- |
| Full Devgraph Python suite | 2,695 passed, 10 existing opt-in skips |
| Repository verification, Ruff and workflow lint | Passed |
| Matched Wallet + secS Rust checks | 25 passed, no skips; includes the independently supplied signature fixture, native v2 CLI, standalone read binary and vendored contract |
| Matched Devgraph native read and Todo checks | 85 passed, no skips, using newly built disposable Wallet/secS drivers |
| Source parity and request vectors | All five retained read files match the paired Wallet; fresh Work/Todo vectors equal retained bytes |
| secS #297 foundation | 811 workspace tests passed, 1 existing ignored; build, formatting and strict Clippy passed; all five authority tests rerun with the independent Wallet fixture supplied |
| Current secS source CI | #297 `d8148f8` passed build/test, audit and WASM; #298 `a1b09ab` passed those checks plus the standalone read lane |
| Devgraph hosted checks at `5ee5539` | Repository and public SDK/package checks passed; companion lane failed before tests because both source App settings are absent |
| Devgraph hosted checks at `c2d5b52` | Repository/protocol and public SDK/package checks passed; companion is intentionally skipped on PR events and still requires configured trusted dispatch |

The complete matched runner records the actual clean Devgraph head, source-set
hash, tool versions, commands, executed counts and disposable binary hashes in
`companion-source.json`. Local source success does not replace the blocked
remote companion lane. Final head checks must be read from the PRs; a later
source or pin change requires affected checks again.

A local shared-cache rerun reused #297's earlier test binary and failed when the
Todo vectors were absent. The runner now explicitly requires the named Todo
grant/rejection cases as well as fresh vectors. Its regression test rejects a
foundation-only binary. The affected source-package cache was cleared before
the clean rerun; no failed result was reported as passing.

Wallet Buildkite #30's existing package evidence has been reviewed against
`f654017`: five Chrome scenarios, two upgrade journeys, independent issuer TLS
and reproducible packaging. The content-tree digest is
`29a846e679f5887723e92868efc7a228acfe3bfa990bcbae0b968b37639115cf`.
These are Linux headed-Chrome tests with disposable profiles; the TLS suite uses
a test CA and the generic presentation suite does not exercise installed secS.
The human explicitly authorized recording this evidence. All five acceptance
fields were submitted, Buildkite #30 passed, and Wallet #32's GitHub status
confirmed success at the unchanged source revision. This clears only Wallet's
manual CI acceptance; no merge, grant, trust or live runtime change followed.

## Initial source composition at `aa3626f`

The candidate composes the native-read integration at `3eea919` with the committed
independent recovery-destination change from `6b82e34`. Unrelated preview changes
are excluded. The generic SDK and provider checks now use Wallet #32 `f654017`;
the secS candidate is #298 `109c0be`. The native SDK lockfile changes only the
generic Wallet dependency. Historical Wallet `3216144` and secS `11e7128`
compatibility dependencies remain separate and unchanged.

The named-v1 compatibility adapter now reports a request/parser mismatch
separately from custody and authority failures. It accepts only a single bounded,
allowlisted JSON error code, rejects duplicate keys and extra fields, suppresses
unrecognized output, and stops oversized or timed-out adapters. It never prints
arbitrary native stderr. A rejected locally valid workflow request stops before
HTTP and retains the original caller's request and idempotency files. This does
not make an older signer support the operation or resolve an earlier unknown
dispatch.

Codex/Hermes guidance distinguishes the explicit generic path and real human
approval from retained named-v1 compatibility. Generic recovery uses fresh
approval for a status-only receipt lookup, never an automatic second mutation.
Request bytes, signing domains, grant ceilings and response schemas are unchanged.

### Initial verification

| Check | Result |
| --- | --- |
| `bash docs/dev/verification.md` | 2,666 Python tests passed, 10 skipped; generated artifacts and Ruff passed |
| Diagnostic/CLI/Arena subset | 50 passed; includes malformed, duplicate, oversized, private-output, timeout and parser-mismatch cases |
| Public Rust workspace, locked dependencies | 29 passed, including protocol and cross-language corpus checks |
| Native SDK with Wallet `f654017` | 17 passed; Clippy passed |
| Public WASM target | `devgraph-web` compilation passed |
| Generic frontend setup/transport/coordinator/boundary suites | 15 passed |
| Exact pinned Wallet provider-profile parser | Devgraph setup fixture and invalid-profile checks passed |
| secS generic authority vectors | 7 passed, with a freshly generated independent Wallet fixture supplied |
| secS native credential CLI | Preflight, approval, retry and denial unit test passed |
| Standalone secS read tool | Compiled; its binary defines no unit tests |
| Vendored read-authority contract | 2 passed; all five vendored files match hashes and exact Wallet Git blobs |
| Python receiver with newly built secS read binary | 3 passed: holder/replay/revocation, Arena filtering before pagination, and policy changes |
| Actual Wallet core → enrollment → secS → guarded reader | 2 passed with disposable identities, including recovery excluding access |

The final two rows separately exercise five native-read cases skipped by the
default Python run. Remaining skips require other optional integration fixtures.
The native SDK's disposable loopback test initially encountered a sandbox port
restriction and passed when rerun with that local fixture permitted. No test
used the live identity, grants or graph.

## Remaining gates

- Current-head maintainer review and checks for both secS PRs and Wallet #32.
  The coordinating source review found no blocking issuer/caller/resource,
  policy, replay or private-file issue in #297; it is not maintainer approval.
- The approved read-only private-source App configuration and a successful
  remote Devgraph companion run at the final pinned head.
- After foundation merges, retarget #298, refresh resulting consumer pins and
  rerun affected checks; current candidate IDs are not merged release IDs.
- Matched packaged artifacts, including the independent read service and its
  native artifacts; source revisions alone are insufficient.
- Full installed browser/terminal qualification in the documented disposable
  macOS guest. No runner is configured. Fixture passes do not satisfy this gate.
- Dependent legacy removal and a fresh final-artifact qualification pass.
- Separately reviewed live activation, current authorization, and a committed
  receipt for the intended workflow transition.

Keep existing live readers and source-independent work available. The live
runtime, trust, credentials, grants and work records were not changed by this
source checkpoint. See [candidate setup](merged-wallet-provider-setup.md) and
[installed qualification](generic-wallet-qualification.md).
