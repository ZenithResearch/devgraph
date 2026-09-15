# Compatibility: v0.6.0

This additive release activates the Arena type introduced in v0.5.0 without
adding Arena to Todo or the five-kind Work API. Existing Work records, parent
edges, requests, signatures, grants, and receipts remain valid. Consumers should
pin this release and its bundle digest before using the Arena runtime contract.

New Arena requests have their own schema, digest domain, operation names, route,
and response DTO. The optional Work `parent.set.previous_arena` field is signed
when present. Requests that omit it retain their original canonical bytes.
Deploy compatible Wallet and secS binaries before enabling Arena grants. Reads
of Arena monitor nodes require consumers that recognize the additive `arena`
category. Migration 26 is forward-only and adds one uniqueness constraint.

The historical v0.1.0 through v0.5.0 bundles are byte-pinned and unchanged.
