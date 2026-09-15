# Changelog

## 0.1.0-beta.1 — 2026-09-15

First public beta, published as a clean source snapshot under AGPL-3.0-only.
Earlier internal history is retained privately.

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
- Removed internal planning/review archives from the public source snapshot.

### Beta limits

The managed host is local and macOS-specific. The synthetic demo is in memory.
Native signing companions are separate prerequisites, not bundled downloads.
Operational records retain the API/storage name `EventReceipt` and are unsigned;
they are not cryptographic receipts. External event delivery is not implemented.
Repository verification does not establish another machine's availability,
backup recovery, or production readiness. See [beta setup](docs/user/beta.md).
