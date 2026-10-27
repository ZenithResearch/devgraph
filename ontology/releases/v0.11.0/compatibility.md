# Compatibility v0.11.0

Additive ontology and private storage/capture implementation. SessionRef and
SessionLink have dedicated storage labels; CodexSessionRef, ClaudeSessionRef and
GrokSessionRef are descriptive specializations of the common storage contract.
HermesSessionRef becomes a subclass of SessionRef without changing legacy Python
fields, stored properties or ATTRIBUTED_TO_HERMES_SESSION edges. Existing readers
continue reading old records. Old-only readers do not see newly typed refs until
they adopt the generic contract; no automatic migration or dual-write is implied.

Migrations 29/30 add uniqueness constraints. All prior migrations and ontology
bundles v0.1.0–v0.10.0 are unchanged. No named Work/Arena operation, credential,
signature domain or admitted receiver permission changes. Local capture does not
write the graph. A source runtime with only 28 supported migrations cannot safely
run against a host after applying 29/30. Deployment and rollback must use the
normal reviewed migration procedure; no live migration ran for this candidate.
