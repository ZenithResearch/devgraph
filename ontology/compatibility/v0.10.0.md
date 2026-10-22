# Compatibility v0.10.0

Additive descriptive vocabulary only. All new classes have runtimeLabel false.
Work kinds, named operations, constraints, signing domains, credentials and graph
storage are unchanged. Existing bundles v0.1.0 through v0.9.0 remain byte-pinned.
Current operation/profile metadata advances ontology_version only.

The distinct application contract zenith.devgraph-owner-setup.v2 explicitly adds
read_operations and an optional exact preset_snapshot; v1 retains its closed
shape, digest and default reads. Older clients keep using v1. Seat references
are descriptive and cannot grant access. Transfer's local preset library does
not publish a server catalog or migrate review-only directory schemas.
