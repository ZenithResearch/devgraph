# Zenith ontology v0.6.0

Arena now has a separate runtime binding, stable records, four signed operations,
and authorized API/CLI reads. Direct `CONTAINS_WORK` edges belong only on
parentless Initiatives and Tasks. Descendants inherit membership through Work
parents. Moves and parent assignment remove obsolete membership atomically.

The existing five-kind Work interface and request bytes remain compatible.
Migration 26 adds unique Arena IDs. Arena permissions require an explicit grant
extension; membership grants no authority. See `arena-runtime.md` for the
wire contract and Gallery example. All v0.1.0–v0.5.0 bundles remain unchanged.
