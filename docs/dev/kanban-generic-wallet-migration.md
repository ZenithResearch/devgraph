# Kanban application-owned credential transport

The implementation lives in public Devgraph PR #10, including the SDK, receiver,
and canonical Todo contract. The private repository is a deprecated archive. Wallet remains application-neutral. The board constructs
requests; secS grants exact operations/resources; Wallet confirms the issuer-bound
disclosure; Devgraph validates workflow/evidence/version gates at commit.

Kanban uses `/assets/credential-v2/` and Wallet's generic capability/presentation
interface. Supported operations include workflow assignment/review/transition,
progress, archive/restore and proposal decisions across permitted Todo kinds.
Capabilities describe implemented operations, not the caller's permissions. secS
preflight checks the current explicit grant before consent. Old v1 grants do not
acquire v2 or workflow permission through renewal. No old signer or read credential
is a fallback when authority is missing.

Unknown outcomes retain the original canonical request/key and actor/profile in
the existing recovery record. The recovery control obtains a fresh approval and
calls status only. It never resubmits a missing mutation. A missing receipt remains
unknown. Old bridge recovery records cannot silently cross connection profiles.
Version conflicts refresh the board and explain that the item changed. Success is
announced only after a committed, matching receipt; cards reload from the reader.

Browser SDK 0.2 uses Devgraph-owned HTTP reads with an explicit in-memory credential,
and the same generic mutation coordinator. Native SDK v2 profiles use fixed
owner-private Wallet/secS binaries and Wallet's interactive terminal confirmation.
The 0.1 compatibility SDK/host remains solely until replacement qualification.

Source and synthetic browser checks are separate from the
[disposable macOS gate](generic-wallet-qualification.md). No installed signing pass
is claimed. Full retirement is a dependent change after that gate passes, followed
by another exact-artifact qualification run.

Merged Wallet setup uses **Load Wallet setup**, **Review provider in Wallet**, then
**Connect Wallet for moves**. Configuration is fetched before the two consent
clicks; Wallet checks browser user activation for each. The provider proposal
contains only this page's origin and request-credential trust. It includes no
membership configuration or receiver grants. Each mutation still has separate
approval and current secS policy checks. See [operator setup](merged-wallet-provider-setup.md).
