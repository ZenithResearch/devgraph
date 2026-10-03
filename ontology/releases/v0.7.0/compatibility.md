# Compatibility: v0.7.0

Workflow metadata and read endpoints are additive. Missing workflow metadata is
preserved; legacy readers and signed response envelopes do not receive additional
fields. New records begin their default workflow in Backlog. Accepted and Archived
remain lifecycle states and never imply workflow Done.

Old request bytes, signature domains and vectors remain identical. New workflow
operations and additional parent pairs require matched Python/Rust, Wallet and secS
versions and explicitly reviewed grants. Renewing an old grant adds no operations.
Forward migration 27 validates metadata without inventing stages. Historical
v0.1.0 through v0.6.0 bundles remain byte-for-byte unchanged.
