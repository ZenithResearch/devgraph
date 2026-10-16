# Devgraph delegated read contract v1

This directory is the versioned, implementation-neutral contract for delegated read access to Devgraph.

The seven v1 scopes are `devgraph.graph.read`, `devgraph.work.read`, `devgraph.arena.read`, `devgraph.observation.read`, `devgraph.material.read`, `devgraph.document.read`, and `devgraph.query.read`.

JSON values are serialized as UTF-8 with object keys sorted lexicographically, no insignificant whitespace, and no non-finite numbers before hashing or signing. Producers must reject unknown fields and consumers must fail closed on unknown schema identifiers, unsupported versions, invalid audience or resource bindings, expired or not-yet-valid credentials, and grant amplification.

`devgraph.query.read` is intentionally exceptional: it is valid only with an unrestricted Work grant containing every Work kind and `include_archived: true`, with neither `work_ids` nor `arena_ids` present. Typed endpoints remain the preferred API.

Self-attenuation and child delegation are not part of this contract until an official released vanilla Dregg SDK exposes and verifies those capabilities. Implementations must not emulate them with internal modules, path dependencies, or unpublished packages.

`integration-manifest.json` pins the SHA-256 digest of each contract artifact and records the released Dregg SDK provenance and capability matrix used for this version.
