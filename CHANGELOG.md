# Changelog

## Todo progress, archive and daily check-in

- Add canonical three-state progress for base Todo and every subtype, preserving legacy signed requests. Base Todos can complete without a subtype workflow.
- Separate archival from progress, preserve guarded subtype completion and proposal dispositions, and report ambiguous history through forward migration 28.
- Add bounded v2 Todo/Kanban reads, priority-based daily counts, consistent reader/topology labels, and remove credential persistence.
- Publish ontology v0.8.0 without rewriting older releases. See `docs/todo-progress-v2.md` for compatibility and installed qualification boundaries.

## [Unreleased]

### Fixed

- Give the enabled credential SDK mount precedence over the composed read-only asset handler — makes SDK/WASM assets reachable while standalone read-only factories retain their allowlist.

- Bind the standing inbox policy decoder before provisioning imports — restores the composed preset/scope catalog without changing authority or stored contracts.

### Added

- Add a separate RFC1918-only runtime v8 listener with guarded inbox and Work routes — enables reviewed LAN activation while keeping the existing read-only/public factories closed and preserving transport limits.


### Changed

- Separate permission presets from global or multi-root resource scope with a searchable inheritance tree — selecting a container covers current/future descendants without expanding the operator ceiling.

- Add durable explicit-validity read/write activation, resource-specific membership and matching browser selection — grant once and redeem finite credentials without conflating timers, legacy contracts or unrelated community membership.


### Added

- Add shared dated/until-revoked standing-grant validity semantics and strict vectors, with finite compatibility adapters — removes ambiguity from the next grant contract without changing historical signed drafts or enabling unreviewed runtime authority.

### Added

- Add an explicit private-LAN read listener without a test shutdown timer — keeps connection setup available while retaining grant expiry, revocation and closed administration/write routes.

### Changed

- Require an explicit standing-grant expiry in the operator inbox and show its separate credential lifetime — removes the hidden one-hour grant default without changing signed contracts or widening policy.

### Added
- Opt-in runtime v7, server-owned presets and shared hierarchy selection with persistent path grant activation — members can request a role and area, operators can review and atomically activate or revoke read/write authority, and resource moves change effective access without reissuing credentials. Legacy signed records retain their original contracts.


### Added

- Add versioned live resource-path read/write enforcement with ancestor-preserving attenuation, serialized projection and pre/post mutation checks — grants follow current and future descendants while moved-out objects lose access, without broadening legacy contracts.

- Add an opt-in bounded HTTPS read pilot with public connection discovery and default-closed operator routes — enable real remote Wallet testing without exposing local mutations or database administration.

### Fixed
- Show the authenticated operator ceiling and narrow read operations/object kinds while retaining canonical signing order — operators can inspect actual policy limits and safely select less access without creating invalid Wallet requests.
- Preserve anonymous JSON metadata discovery on free ngrok HTTPS hosts with a non-secret transport hint — cookie-free monitor requests otherwise receive the tunnel browser notice and cannot open the operator inbox.
- Decode persisted monitor enrollment ceilings with the versioned six-kind grant contract — repeat Todo read enrollment no longer fails after consent, while renewal narrowing and the historical five-kind decoder remain enforced.
- Distinguish read-review refresh, Wallet opening, issuer enrollment and credential acceptance — an expired review no longer looks like a missing Wallet dialog, and preflight failures remain actionable without announcing a connection.
- Keep operator Wallet confirmation beside the selected permissions, freeze prepared selections, and explicitly refresh expired unsigned challenges without replacing action IDs — operators no longer have to find a second approval button elsewhere on the page or restart a timed-out flow.
- Detect Chrome Wallet automatically and embed Wallet-owned recovery on a separately trusted origin when selected — removes the redundant chooser without exposing recovery inputs to the monitor or bypassing consent.
- Keep unassigned Wallets connected while operators configure scoped access, disclose only public member/device setup details, and refresh expired challenges before signing — setup and delayed approvals no longer require restarting the Wallet connection.
- Preserve unknown mutation outcomes when Wallet locks during status recovery, expose bounded actionable Wallet errors, and canonicalize native temporary paths so macOS aliases do not fail secS secure-path checks. Web SDK preview.3 includes the new runtime module in artifact qualification hashes.
- Update the SDK build and consumer tooling and enforce npm audits in Buildkite — patched source-map-js/esbuild dependencies and retained audit reports keep vulnerable development dependencies from passing release checks.
- Accept an omitted Todo filter on Python 3.10 — default API and CLI reads use the documented defaults while malformed nonempty filters remain rejected.
- Validate Kanban and SDK credential results through the same Rust/WASM decoder — partial receipts cannot announce success and failed status lookups cannot release an uncertain mutation.
- Recognize bounded receiver rejections on initial execution — unmet workflow requirements no longer trap corrected requests behind reconciliation.
- Audit all tracked Rust lockfiles and consume the coordinated patched Wallet parser — native and standalone packages participate in the dependency gate.


### Added
- Add opt-in runtime v6 for durable, holder-wide write assignments, pre-consent native admission checks, atomic activation/revocation and guarded generic receiver routes. Arena restrictions intersect object scope, existing read grants are preserved, and v1–v5 behavior/signing domains remain unchanged. Source checks do not activate or qualify an installed service.
- Add an explicit receiver-owned scoped write adapter with intersected object/Arena grants, transactional checks before and after moves, guarded provenance and status-only receipt recovery. The foundation remains available independently; the separate runtime v6 path adds activation and guarded composition with explicit owner setup.
- Add generic Wallet request consent and an independently authorized monitor access inbox with attenuated grant review, versioned decisions and status-only recovery — operators can review exact permissions without confusing selection approval with credential activation.
- Add an explicitly composed, member-authenticated access-request inbox with durable status recovery and bounded private operator projection — requesting permissions no longer needs to be confused with issuing them; Wallet consent and grant activation remain separate integration steps.
- Connect the monitor and read-only board through generic Wallet read approval, with Wallet-owned login/recovery and six-kind per-member scoped projections. Explicit runtime v3 and v2 policy opt-in preserve existing grants and signing formats; observations/documents are not silently included.
- Add issuer-owned member access drafts and opt-in per-member signed reads, so operators can narrow read/write selections without issuing a broad credential or granting new rights through renewal. Existing signing formats remain unchanged; unsupported restrictions fail closed.
- Distinguish native request compatibility, custody and authority failures using bounded public diagnostics — valid workflow requests rejected by older companions no longer suggest resetting signing keys.
- Reconcile the generic native dependency and release candidate with Wallet's holder-bound read contract and secS's standalone read authority — preserve phone reads when qualifying the coordinated signing upgrade.
- Add the shared holder-bound read issuer/secS/receiver path and native-v2 composition — enforce approved Work/Arena scope before pagination without an owner bearer fallback.
- Allow an explicit external recovery destination with an internal-SSD database — preserves two-physical-disk recovery coverage without depending on the live data root being external.

- Reconcile the generic integration guidance with merged Wallet and its runtime provider approvals — the old build-time pins and Devgraph-specific bridge are no longer the supported Wallet setup.

- Update the target Kanban/SDK architecture to generic Wallet approval with Devgraph-owned transport and a disposable macOS VM release gate — this prevents the superseded native bridge and source-only checks from being mistaken for installed qualification.

- Move project scope controls into one compact Filter popover with an active-filter count, freeing the project list from two permanent dropdowns.

- Limit the daily project list to Projects and add saved Arena/Initiative filters using containment and inherited Arena membership; dependency links never imply membership.

- Make the daily Todo queue a compact vertical panel and give the main check-in area to scoped project progress, reviews and dependency follow-up. Preserve exact Todo counts and keep unclassified history explicit.


- Add explicit native Wallet read enrollment with independent membership trust, scoped grants, replay protection and guarded read projections — support Zenith iOS without distributing owner keys or adding mutation authority.

- Add the separately signed Work v2 protocol with base Todo, progress attestations, restoration and proposal rejection — shared consumers can adopt progress without changing historical v1 bytes.

- Publish the shared Rust Work/Arena request protocol and cross-language synthetic fixtures — native and WASM consumers can pin public source without importing private history or losing Arena resource bindings.

- Kanban reading page, versioned Vibe CEO/execution workflows, signed approval and transition operations, bounded board API, flexible parentage and ontology v0.7.0. Legacy work remains unclassified. Installed Chrome signing still requires isolated qualification.

- Shared Rust workflow request parsing and cross-language vectors, preserving old signing bytes so Wallet and secS can admit the same explicit workflow operations.

## 0.1.0-beta.2 — 2026-09-15

First public beta, published as a clean source snapshot under AGPL-3.0-only.
Earlier internal history is retained privately. The beta.1 tag identifies the
initial source candidate; its download release was not published.

### Included

- Canonical Work objects: Proposals, Initiatives, Projects, Issues and Tasks,
  with descriptions, dependencies, lifecycle and versioned mutations.
- Arenas with direct membership on parentless Initiatives and Tasks and
  inherited membership for their descendants; ontology bundles through v0.6.0.
- Read-only graph monitor with zoom, pan, resizing, Arena and node-class filters,
  search, details, observations, supporting documents and operational records.
- Local Python API and CLI, bounded reads, and macOS persistent-host tooling.
- Signed Work and Arena operation interfaces using separately installed native
  Wallet/secS components, an operator-selected identity and explicit grants.
- Codex skill/plugin and Hermes standalone plugin, sharing the canonical skill.
- Generalist and programmer guides, synthetic demo, package checksums and source.

### Publication hardening

- Updated FastAPI and Starlette to address the dependency advisories identified
  in the beta audit while preserving the Python 3.10 baseline.
- Pinned CI actions, limited checkout credentials, and bounded CI execution.
- Made expression function dispatch explicit and added tests rejecting prototype
  names and predictor overrides; confirmed provisioning output contains state
  labels rather than credential values.
- Removed internal planning/review archives from the public source snapshot.

### Beta limits

The managed host is local and macOS-specific. The synthetic demo is in memory.
Native signing companions are separate prerequisites, not bundled downloads.
Operational records retain the API/storage name `EventReceipt` and are unsigned;
they are not cryptographic receipts. External event delivery is not implemented.
Repository verification does not establish another machine's availability,
backup recovery, or production readiness. See [beta setup](docs/user/beta.md).

- Why: a lost review response can outlive its page. Restore its exact request identity after status recovery, and reject changed challenge/selection details before further consent or decision.

- Why: an operator-approved request must become usable without manually editing member assignments. Add explicit runtime v5 read-grant activation/revocation, exact replacement consent, atomic decisions/receipts and reuse through the existing secS issuer. Preserve earlier runtime behavior and reject unsupported partial write activation before consent.

- Why: status recovery returns an historical receipt, not a fresh assertion that a grant is still active. Label recovered activations accordingly and exercise lost activation responses without resubmission.
