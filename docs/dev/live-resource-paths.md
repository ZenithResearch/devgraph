# Live resource paths

The scope is evaluated against canonical containment **at the time of access**,
not expanded into a snapshot at grant or credential issuance. A selected container
and its current descendants are eligible, subject to the same grant's operations,
kinds, explicit IDs, Arena restrictions, archival policy, validity and revocation.
A new descendant becomes eligible without changing the grant. A resource moved
outside the selected path stops being eligible even with an unexpired credential.

A path uses stable typed IDs, for example:

```json
{"schema":"devgraph.resource-path.v1","path":["Arena/example","Initiative/roadmap","Project/mobile"]}
```

Every selected ancestor remains a condition. If Project/mobile moves outside
Arena/example or Initiative/roadmap, that path grants no access to it or its
children. A grant for Project/mobile alone follows the project wherever its
valid canonical ancestry places it. The operator must see this distinction.
Paths are OR alternatives; anchors within each path are AND conditions. Empty
path arrays in a path grant are invalid. Labels and textual ID prefixes confer
no authority. Narrowing must preserve every ancestor of a covering parent path;
a lookup of today's membership cannot justify dropping a durable restriction.

The primary anchors are Arena, Initiative and Project. Existing legal containment
still applies, including standalone Tasks and skipped levels; this does not add
new parent relationships to the ontology. Finer explicit IDs intersect the path.
Provenance records and base Todos do not acquire ancestry through made-up edges.
The resolver rejects cycles, duplicate/multiple parents, invalid parent kinds,
missing ancestors and illegal direct Arena memberships, including corruption
above the selected anchor. Relationship projections include only admitted nodes.

Read filtering happens inside the storage query before limits, counts and cursor
construction. The entire path projection and its edges share the Work mutation
transaction, preventing reparenting between admission and disclosure. Writes
check the current path after native verification and inside that same serialized
transaction, before mutation and before commit. One grant must cover both states;
separate path grants cannot launder a move. A current scope is also required for
idempotent receipt recovery. A path grant alone cannot create a missing unattached
object: an atomic in-path create protocol needs separate design and qualification.

## Compatibility and rollout status

Source contracts are `devgraph.path-read-grant.v1`,
`devgraph.path-write-grant.v1` and `devgraph.receiver-write-policy.v2`.
The new receiver policy can combine path-restricted Work grants with separate
explicit provenance grants; it never infers provenance authority from a path.
Signed application constraints must preserve the entire canonical path object.
The shared Rust read-authority format keeps application constraints as exact JSON;
Devgraph remains responsible for their meaning. Native prefix rules cannot
represent these predicates and export refuses to flatten them.

This is the source enforcement foundation. **The installed runtime, enrollment
and durable operator/request contracts do not yet admit these path grants.**
Legacy draft, enrollment and receiver parsers stay closed rather than silently
serializing away the new fields. Existing grants keep their old semantics.
The preset catalog, hierarchy selector, durable activation/version migration,
Wallet disclosure and native client compatibility must be integrated before this
capability is advertised or deployed. No current member authority is broadened.

Schemas and examples are in `docs/contracts/resource-path/v1/`. Unit coverage
includes existing/future descendants, reparenting, ancestor moves, invalid chains,
pre-pagination/count filtering, exact attenuation, signed write denial, rollback,
serialized reads and legacy rejection. The opt-in Neo4j parity test uses an empty,
separate fixture database; synthetic success is not live Wallet qualification.
