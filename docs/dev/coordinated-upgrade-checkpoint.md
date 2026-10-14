# Coordinated upgrade: source checkpoints

14 October 2026. Source implementation and disposable fixture evidence only.
These checkpoints do not qualify or activate the installed mutation chain.

## Companion CI and review follow-up

The current candidate pins Wallet #32 `f654017` and secS #298 `a1b09ab`, which
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
| Full Devgraph Python suite | 2,694 passed, 10 existing opt-in skips |
| Repository verification, Ruff and workflow lint | Passed |
| Matched Wallet + secS Rust checks | 25 passed, no skips; includes the independently supplied signature fixture, native v2 CLI, standalone read binary and vendored contract |
| Matched Devgraph native read and Todo checks | 85 passed, no skips, using newly built disposable Wallet/secS drivers |
| Source parity and request vectors | All five retained read files match the paired Wallet; fresh Work/Todo vectors equal retained bytes |
| secS #297 foundation | 811 workspace tests passed, 1 existing ignored; build, formatting and strict Clippy passed; all five authority tests rerun with the independent Wallet fixture supplied |
| secS #298 source CI | Build/test, audit, WASM and standalone read lanes passed on `f5f2ece`; `a1b09ab` composes the same source bytes with #297 and changes only the changelog relative to that head |
| Devgraph hosted checks at `5ee5539` | Repository and public SDK/package checks passed; companion lane failed before tests because both source App settings are absent |

The complete matched runner records the actual clean Devgraph head, source-set
hash, tool versions, commands, executed counts and disposable binary hashes in
`companion-source.json`. Local source success does not replace the blocked
remote companion lane. Final head checks must be read from the PRs; a later
source or pin change requires affected checks again.

Wallet Buildkite #30's existing package evidence has been reviewed against
`f654017`: five Chrome scenarios, two upgrade journeys, independent issuer TLS
and reproducible packaging. The content-tree digest is
`29a846e679f5887723e92868efc7a228acfe3bfa990bcbae0b968b37639115cf`.
These are Linux headed-Chrome tests with disposable profiles; the TLS suite uses
a test CA and the generic presentation suite does not exercise installed secS.
Recording the manual acceptance is pending explicit authorization. No acceptance
submission, merge, grant, trust or live runtime change is implied by this review.

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
- Authorized recording of Wallet #32's reviewed Buildkite acceptance evidence.
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
