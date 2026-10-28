# Source-owned session references v0.11.0

Adds general SessionRef identity, Hermes/Codex/Claude/Grok specializations,
witnessed SessionLink delegation/fork/runtime relationships, explicit Work attribution,
opt-in local metadata capture, exact signed Work v2 session attachment and
authenticated canonical metadata reads. Existing HermesSessionRef records and edges
retain their bytes and meaning. New source identity is independent of model
provider and importing authority. See session-references.md and compatibility.md.

This source candidate is not an installed integration, grant change, host
migration or public web publication. New storage constraints are forward-only;
no transcript importer is enabled. A standalone metadata observer and companion
reader are available; native binding coverage depends on the installed Hermes
producer, and canonical writes require separately approved receiver authority.
