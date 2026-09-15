# Zenith ontology v0.4.0 compatibility

v0.4.0 is additive over v0.3.0. The immutable v0.1.0, v0.2.0, and v0.3.0
releases remain supported and byte-identical.

- `Actor`, `Person`, `Agent`, and `Organization` are new canonical classes.
- `Person`, `Agent`, and `Organization` are subclasses of `Actor`.
- `MEMBER_OF`, `OPERATED_BY`, `ASSIGNED_TO`, `OWNED_BY`, and `ATTRIBUTED_TO`
  are new descriptive object properties.
- All new Rolodex classes have `runtimeLabel: false`.
- Existing operation names, request/response schemas, Work kinds, receipt
  decoding, runtime labels, constraints, migrations, and lifecycle semantics
  are unchanged.
- Existing `actor_id` strings remain valid. Consumers may resolve them to an
  Actor in the future but must not infer identity or authority from a directory
  record or caller payload.

Consumers that do not understand the new classes and predicates may ignore
them while continuing to use existing operations. Consumers must keep pinning
both the ontology version and manifest bundle digest.
