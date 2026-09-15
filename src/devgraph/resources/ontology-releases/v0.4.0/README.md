# Zenith Ontology v0.4.0

v0.4.0 adds the canonical Rolodex vocabulary: `Actor` with the intended
subclasses `Person`, `Agent`, and `Organization`, plus `MEMBER_OF`,
`OPERATED_BY`, `ASSIGNED_TO`, `OWNED_BY`, and `ATTRIBUTED_TO` relationships.
Organizations are Actors because collective entities can own and receive
attribution for work.

This release separates directory identity from cryptographic authority. An
Actor is not an authentication Subject, keypair, credential, wallet, secS
capability, or `.castaway` vault. Directory relationships are descriptive and
cannot grant mutation authority or widen scopes.

The Rolodex classes are published with `runtimeLabel: false`. No Neo4j label,
constraint, migration, generic Work kind, HTTP route, lifecycle, or stored
decoder changes in this release. Reified Membership/Role history, private
contact storage, and visibility policy remain deferred to an explicit runtime
and privacy design.

The v0.1.0, v0.2.0, and v0.3.0 bundles are unchanged and remain supported.
