# Zenith Ontology v0.1.0

This is the first canonical, machine-readable Zenith ontology release.

Devgraph is the authoritative source. `zenith-research.ca` is the public distribution surface. Consumers should resolve the current release through `/.well-known/zenith-ontology`, pin both `version` and `bundle_digest`, and use versioned URLs for reproducible work.

The release contains:

- JSON-LD context and ontology graph;
- transport-neutral Devgraph semantic operation names and schema identifiers;
- the Zenith Repository filesystem contract;
- human-readable class, predicate, taxonomy, readiness, and invariant documentation;
- the generated Neo4j constraint mirror for the bounded Devgraph runtime subset;
- compatibility notes and a checksum manifest.

The ontology is broader than the Devgraph Neo4j runtime. `runtimeLabel: false` means a canonical concept is not a first-class stored label in the current runtime. In particular, `ZenithRepository` is canonical vocabulary but projects into Devgraph through `ExternalLink`, optional `SyncShadow`, and `Artifact` evidence.

Numeric secS opcodes are not ontology identifiers. Receiver-local manifests bind those opcodes to the semantic operation names published in `operations.json`.
