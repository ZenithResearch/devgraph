# Zenith Ontology v0.5.0

v0.5.0 introduces **Arenas**: continuing areas of responsibility that group
Initiatives and parentless Tasks through `CONTAINS_WORK`. Todo is the common
base; Task is the concrete executable to-do in the Work interface.

Direct membership is optional and limited to one Arena per member. Work
descendants inherit their root's Arena without additional stored membership
edges. The existing Initiative → Project → Issue → Task hierarchy stays intact.
See `arenas.md` and the normative `arena-contract.json` for eligibility,
parent changes, moves, archival, and invalid-graph behavior.

The Rolodex now prefers **Entity** as the directory base for Person, Agent,
and Organization. Actor remains an equivalent compatibility name with the
same published identifier. Directory membership does not create authority.

Arena and Rolodex additions have `runtimeLabel: false`. This release adds no
Neo4j label, constraint, migration, Work kind, HTTP route, or monitor control.
Existing operation schemas and persisted Work/receipt decoders are unchanged.
The v0.1.0 through v0.4.0 bundles remain byte-identical and supported.
