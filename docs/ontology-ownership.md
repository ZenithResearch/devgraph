# Core and extension ownership

ZenithResearch/zenith-ontology owns the shared core vocabulary formerly authored in DevGraph v0.12.0. The successor is Zenith core v0.13.0; its 86 term records and parent axioms are unchanged. DevGraph imports that exact release and owns its operation, validation and storage/runtime profiles.

Author new shared definitions in Zenith, publish a new immutable core release, then explicitly update ontology/extension/publication.json and ontology/ownership.json when DevGraph adopts it. Run `node scripts/build_ontology_extension.mjs` and `node scripts/build_ontology_extension.mjs --check`; changed bytes at an existing extension version are rejected. Never regenerate Zenith vocabulary from DevGraph. The legacy builder is retained for `--check` only.

The extension initially adds no class definitions; it declares a pinned OWL import and carries the existing runtime profile files byte for byte. Independent core updates do not automatically upgrade DevGraph, Zendo or other dependents. Existing immutable releases, packaged CLI runtime pins and database schemas stay compatible. `devgraph ontology` reports both the packaged runtime snapshot and additive current vocabulary/extension metadata. This source change does not deploy a host or migrate its graph.

## Distribution and compatibility contract

The single authored `ontology/ownership.json` lock is checked against the exact
core import before any release or generated metadata writes. The extension
builder generates the source index, packaged ownership copy, package-relative
source index and read-only core/extension resources. Run it after an explicit
core adoption, then run check mode; never edit generated copies independently.
The selected closure is shipped in both wheel and sdist. Old runtime resources
remain for compatibility; source ownership does not change runtime admission.

`devgraph ontology` keeps its original runtime-v1 fields and historical issuer.
`devgraph ontology --format sources-v1` is the opt-in
`devgraph.ontology-sources.v1` capability, available from CLI 0.1.0b3. It verifies
installed manifests, complete exact imports and resource inventories offline,
then reports package-relative descriptors and resolved installed paths. It
does not establish current public URL availability or website acceptance.
Codex/Hermes archives use this capability and ship no competing ontology copy.
Older/absent CLIs require a supported CLI install; unknown schemas are unsupported.

The beta manifest v2 derives core, extension and runtime descriptors from the
packaged index. Distribution/plugin versions advance independently of immutable
ontology releases. Qualify actual wheel/sdist installations outside the checkout
with `python scripts/qualify_ontology_install.py --python /absolute/venv/bin/python`.
The installer and live/native registration rollout remain explicit later steps.

Root `ontology/discovery.json` and the legacy ontology files retain the v0.12
runtime contract and issuer. They are superseded only as the shared-core
authoring source. Use `ontology/source-index.v1.json` for adopted source roles;
use `ontology/extension-discovery.json` for the extension. Keep immutable historical
releases and all exact dependency closures. Do not rewrite their issuer claims.
