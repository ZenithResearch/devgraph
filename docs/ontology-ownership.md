# Core and extension ownership

[Zenith normalized v0.1.0](https://github.com/ZenithResearch/zenith-ontology-normalized/releases/tag/v0.1.0)
is Devgraph's adopted vocabulary. Devgraph extension v0.2.0 imports it and owns
its local class declarations, operation contracts, and runtime profiles.
The normalized release has a new ontology identity and retains the shared
term namespace. Legacy Zenith v0.13.0 and extension v0.1.0 remain available
with their original identities and hashes.

Publish an immutable shared release before adopting it in Devgraph. Update
`ontology/extension/publication.json` and `ontology/ownership.json` with an
exact pin, and vendor the release bytes. The canonical IRIs identify the
documents inside the archive. The archive URL in the ownership lock provides a
separate distribution location. Run `node scripts/build_ontology_extension.mjs`
and then its `--check` mode. The legacy Devgraph builder supports `--check`
for the historical runtime bundle.

The [extension](../ontology/extension/README.md) describes the local classes.
[Legacy term mappings](../ontology/extension/legacy-term-mappings.json) record
the boundary for each of the 22 older terms outside the normalized bundle.
Compatibility readers continue to accept historical identifiers. Graph labels
and operation payloads continue to follow the versioned runtime contract.
Keep the legacy and normalized RDF import closures separate. Coordinate
changes to the exact legacy Seat IRI and approved preset snapshots with the
configuration and directory readers before migrating any data.

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
