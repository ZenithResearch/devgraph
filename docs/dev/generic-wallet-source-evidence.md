# Generic Wallet composition — source evidence

Historical source evidence from the deprecated archive, before transfer into
public Devgraph. It does not qualify the current public artifact. See
[public integration](public-wallet-integration.md) for current delivery status.

Historical source-check snapshot before the merged-Wallet/provider-profile update.
See [the reconciliation](merged-wallet-provider-setup.md) for the new source boundary;
these earlier test counts do not qualify changed artifacts.

This records source checks for the composed #66/#67 candidate, public #10 and
secS #298. Dependency revisions are pinned in `generic-wallet-source-set.json`.
These results are not installed signing qualification or release authorization.

| Check | Result |
|---|---|
| Private Devgraph repository verification | 2,607 Python tests passed, 5 skipped; generated ontology/tokens and Ruff passed |
| Frontend Node suites | 223 passed |
| Private Rust workspace, all features | 68 tests passed; includes independent verification of 23 historical and 48 new secS authority vectors |
| Rust Clippy and WASM target | Passed |
| Web SDK actual-WASM unit/transport suites | 24 passed |
| Packed SDK external consumer | SSR/ESM, strict TypeScript, Vite and Chromium/Firefox/WebKit passed, including generic HTTP reads; fixture transport only |
| SDK core browser fixtures | Chromium, Firefox and WebKit passed; disposable browsers and profiles only |
| secS workspace | 813 tests passed; locked build, Clippy and host/WASM documentation assembly passed |
| Independent Wallet → secS vectors | Explicit generic Wallet fixture from pinned #28 passed, including exact transcripts and signatures |
| Public Devgraph documentation checks | 59 passed |
| Disposable macOS installed chain | **Blocked: no VM configured** |
| Final legacy-removal artifacts | **Gated: no replacement installed pass** |
| Live activation | **Out of scope; unchanged** |

New tests exercise canonical Todo create/progress/archive/restore through generic
authority; status-only receipt reconciliation; contract/transport version separation;
read-key denial; shared workflow/progress signatures; memory-only Wallet-independent
SDK reads; approval denial/cancellation; uncertain dispatch and fresh approval;
strict raw result decoding; and refusal of incomplete/changed qualification bundles.
Existing workflow/migration suites retain approval ordering, evidence freshness,
independent parent gates and unclassified legacy behavior.

The migration code was not run against live storage. Actual persistent historical
fixtures, rollback, loaded Chrome and interactive terminal/native checks remain
mandatory VM cases. No production keys, grants, trust, migration or service changes
were made. The qualification tool records human-observed installed evidence and
checks artifact/evidence hashes; it is not an automated VM/scenario executor.

The separate retirement inventory identifies active legacy code and packaging to
remove after replacement qualification. It is an inventory, not a claim that those
removals have happened. Generic clients already use application-owned reads and
approval; compatibility entrypoints remain until that gate is satisfied.
