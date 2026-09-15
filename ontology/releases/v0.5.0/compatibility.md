# Zenith ontology v0.5.0 compatibility

v0.5.0 adds Arena grouping and the preferred Rolodex name Entity. Published
v0.1.0, v0.2.0, v0.3.0, and v0.4.0 bundles remain supported and byte-identical.

- `Arena` is a new canonical class outside `Todo`, with `runtimeLabel: false`.
- `CONTAINS_WORK` is an Arena-to-Work predicate. The normative
  `arena-contract.json` profile restricts direct members to parentless
  Initiatives and Tasks, with zero or one direct Arena per member.
- Existing Work parentage remains `HAS_CHILD`; Arena membership does not
  broaden the Initiative → Project → Issue → Task parent rules.
- `Entity` is the preferred common directory base for `Person`, `Agent`, and
  `Organization`. The published `Actor` identifier remains present with an
  `owl:equivalentClass` link to `Entity`. Consumers need not rewrite existing
  Actor references or remint identities. The current JSON-LD context adds
  `equivalentClass` as an ID-valued OWL term.
- All Rolodex classes and the compatibility name remain non-runtime.
- Existing `actor_id` strings, operation names, request/response schemas,
  Work kinds, receipt decoding, runtime labels, constraints, migrations,
  lifecycle rules, and authorization remain unchanged.

An old consumer can retain its v0.4.0 bundle and Actor vocabulary. A consumer
adopting v0.5.0 should understand the Entity compatibility mapping and resolve
the new Arena profile from the manifest before interpreting membership. The
JSON-LD domain/range alone does not express the parentless or cardinality
rules. Consumers must keep pinning both version and manifest bundle digest.

This release supplies vocabulary and containment rules, not a live Arena
API, storage schema, or UI. No stored data migration is required.
