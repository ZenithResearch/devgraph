# Core and extension ownership

ZenithResearch/zenith-ontology owns the shared core vocabulary formerly authored in DevGraph v0.12.0. The successor is Zenith core v0.13.0; its 86 term records and parent axioms are unchanged. DevGraph imports that exact release and owns its operation, validation and storage/runtime profiles.

Author new shared definitions in Zenith, publish a new immutable core release, then explicitly update ontology/extension/publication.json and ontology/ownership.json when DevGraph adopts it. Run `node scripts/build_ontology_extension.mjs` and `node scripts/build_ontology_extension.mjs --check`; changed bytes at an existing extension version are rejected. Never regenerate Zenith vocabulary from DevGraph. The legacy builder is retained for `--check` only.

The extension initially adds no class definitions; it declares a pinned OWL import and carries the existing runtime profile files byte for byte. Independent core updates do not automatically upgrade DevGraph, Zendo or other dependents. Existing immutable releases, packaged CLI runtime pins and database schemas stay compatible. `devgraph ontology` reports both the packaged runtime snapshot and additive current vocabulary/extension metadata. This source change does not deploy a host or migrate its graph.
