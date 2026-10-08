# Generic Wallet installed release gate

Status: **blocked — no disposable macOS VM is configured for this task**. Source
checks and synthetic Chrome tests cannot clear this gate. The live account,
installation, keys, trust, grants, receiver and data are outside this delivery.

## Isolation and candidate preparation

Use a clean Apple Virtualization macOS guest with its own non-root OS account,
Chrome profile, storage and loopback Devgraph receiver on **8080**. Do not mount
host credential directories. secS uses the OS account database; changing HOME or
other environment variables is not isolation. Snapshot the clean guest before
provisioning. Rollback scenarios restore only disposable guest snapshots.

`scripts/qualification/macos_gate.py init-guest` requires Darwin, a VirtualMac
hardware model, matching OS home, no existing Zenith installation and free port
8080. It refuses a physical Mac before creating the guest marker. The harness is a
guided operator-observation recorder and artifact admission check. It does not
create a VM or automate Chrome approval, fixture provisioning, or Neo4j setup.
Those remain installed qualification work, not evidence implied by the harness.

Provision disposable Wallet custody and secS identities only inside that guest.
Use the existing explicit operator setup commands, selecting each required
workflow/progress operation and resource. Register exact browser and terminal
callers and pin the disposable issuer in Devgraph and Wallet. Do not expand a
preexisting grant. Keep all private setup files owner-only and outside evidence.

Use the canonical, unchanged Wallet package from merged `ff8de6ddc04ac2fb6b39408274276bc4ce984d42`.
Its fresh browser installation has **zero trusted providers**. Do not rebuild it
with `CASTALIA_PRESENTATION_TRUST_PINS_JSON` or inject a trust asset. Inside the
guest, explicitly approve the disposable issuer through Wallet's runtime provider
profile UI, and separately approve the browser connection. Follow the
[provider setup guide](merged-wallet-provider-setup.md). Terminal signing retains
its independent owner-private trust file and interactive approval requirement.

Record the public profile, public pin fingerprints, selected request-credential
purpose, exact origin, and the separate approvals in the scenario evidence. Do not
record custody, read credentials, or private grant files. A package change requires
a new candidate; trust changes require repeating affected setup/revocation cases.
Record the Wallet build toolchain and preserve custody, registration, membership,
Files and generic-presentation regression coverage. Wallet Buildkite #28's generic
application pass is companion evidence, not a Devgraph installed-chain pass.

## Bundle and guided run

Create a dedicated bundle containing only `candidate.json` and its declared
artifacts. The candidate schema is `devgraph.installed-candidate.v2`:

- `sources`: exactly `devgraph_public`, `wallet`, `secs`, each an
  immutable 40-character lowercase Git revision.
- `artifacts`: role → relative file → SHA256. Required roles are `wallet_signer`,
  `secs_authority`, `wallet_extension`, `devgraph_wheel`, `web_sdk`. Each native
  role has exactly one binary. Inventory **every** extension/package member and
  all optional dependency wheels/tooling included in the bundle; undeclared or
  symlink members fail admission.

Build from clean pinned checkouts. Keep the build provenance (compiler, Node,
Python, Chrome and package manager versions; lockfile hashes; build commands;
package/binary hashes) in redacted evidence beside the report, outside the bundle.
Do not substitute a developer checkout for a declared installed artifact.

In the guest:

```text
python3 scripts/qualification/macos_gate.py begin --bundle /absolute/candidate --report /absolute/evidence/report.json
python3 scripts/qualification/macos_gate.py install-native --bundle /absolute/candidate
```

Native installation refuses existing destinations and copies only the exact
candidate binaries into the guest's fixed owner-private paths. Install the wheel
in a fresh guest venv, the SDK tarball into an isolated consumer, and load the exact
extension directory in a fresh Chrome profile. Record installed file inventories,
not just archive hashes; compare them before/after the run and include those
comparisons in evidence. Keep the receiver and persistence entirely disposable.
The native installer is not a full application/provisioning installer.

Run both actual paths with fresh interactive consent:

1. Kanban/web SDK → generic Wallet extension → Devgraph HTTP → native secS →
   guarded receiver.
2. Terminal/native SDK → issuer credential → generic Wallet terminal approval →
   native secS → guarded receiver.

For each case name exposed by `--help`, record the observed result and redacted
supporting evidence:

```text
python3 scripts/qualification/macos_gate.py record --bundle /absolute/candidate --report /absolute/evidence/report.json --case browser-todo --outcome passed --evidence /absolute/evidence/browser-todo.json --note "Actual Chrome approval, canonical receipt and subsequent read checked"
```

`--outcome blocked` or `failed` is required when a scenario did not pass. Fixture
results must not be recorded as installed observations. Evidence includes exact
artifacts/tool versions, steps, expected/observed outcomes and public/redacted
receipt bindings, never bearer credentials, private keys or private grant files.
Record each subcase individually inside the relevant scenario evidence.

## Mandatory scenario matrix

| Case | Required installed observations |
|---|---|
| provider-runtime-setup | Fresh package has no trust; read without Wallet; connection and request-credential trust require separate clicks; neither changes grants; approved exact profile works; denial, missing profile, changed key and revoked trust fail without legacy fallback |
| browser-todo | Every allowed operation and all six types; simple Todo done without workflow; canonical results |
| browser-workflows | Both workflows; approval ordering, every legal transition, rejection/rework, Waiting/resume, stale evidence, guarded done |
| terminal-todo | Generic interactive approval for canonical requests; unattended invocation denied |
| native-sdk-workflows | Actual SDK → packaged terminal Wallet → secS → receiver, including result validation |
| archive-restore | Independent archival/restoration preserves progress, workflow and evidence |
| proposal-decisions | Acceptance/rejection provenance; linked delivery remains independent |
| parent-and-evidence-gates | Permitted parent pairs, explicit resource authority, independent parent approvals |
| cancelled-approval / locked-wallet | No mutation or later dispatch after cancellation; lock/unlock behavior |
| navigation-disconnect / worker-restart | Abandoned approval cannot execute; uncertain dispatched request retained |
| expired-and-revoked-authority | Expiry, policy changes and revoked grants deny at preflight and execution |
| wrong-caller-and-resource | Caller/origin, holder, request and resource substitutions denied |
| replay-and-version-conflict | Replay binding enforced; current-version conflict refreshes without success |
| unknown-before-commit / unknown-after-commit | Fault injection before dispatch/after commit; fresh approval then status only; exactly one mutation or explicit unknown; original receipt returned |
| read-credential-write-denial | Read bearer cannot authorize any mutation or issuer operation |
| sdk-reads-pagination-disposal | Wallet-independent reads, bounded pages, full count agreement and credential disposal |
| persistent-migration-ambiguity-and-rollback | Real disposable historical store; explicit mappings only, ambiguous records unclassified; snapshot rollback |
| wallet-custody-registration-files | Existing Wallet custody, recovery, registration, membership and Files regressions |
| generic-two-applications | Generic presentation contract still works for another application |

## Release and retirement gate

`finish` requires every installed case to pass and unchanged bundle hashes.
`verify` additionally checks every evidence-file hash and can run on CI without
VM access. Neither command manufactures evidence or verifies human observations
cryptographically. This is a required recorded manual acceptance gate, not a
substitute for running the scenarios. Peer review must confirm installed-file
comparisons, receipts and full subcase coverage before admitting a release.

```text
python3 scripts/qualification/macos_gate.py finish --bundle /absolute/candidate --report /absolute/evidence/report.json
python3 scripts/qualification/macos_gate.py verify --bundle /absolute/candidate --report /absolute/evidence/report.json
```

The manually invoked `installed-generic-wallet-gate` CI workflow verifies an
uploaded exact-artifact evidence bundle. Repository branch protection must require
that gate for release; configuring remote protection is outside this source change.
Do not release the SDK or merge Devgraph/secS retirement while the gate is blocked.
Wallet has independently merged its adapter removal; only its generic package is
a replacement candidate. That merge does not waive this gate.

After the first pass, apply the [separate retirement inventory](generic-wallet-retirement.md),
package new candidates, and repeat **all** cases. Evidence from before any packaged
source change is insufficient. Live activation remains a separate future action.
