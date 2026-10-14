# Coordinated upgrade: source checkpoint

14 October 2026. Source implementation and disposable fixture evidence only.
This checkpoint does not qualify or activate the installed mutation chain.

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

## Verification

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

- secS #297 review; Wallet #32 review and pending Buildkite result.
- secS #298's stacked-PR and standalone-workspace CI coverage.
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
