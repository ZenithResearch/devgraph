# Delegated read integration

The opt-in delegated read API and its tests now live in public Devgraph. Historical
archive PR #63 is superseded by this source transfer; it is not an active dependency.

`DEVGRAPH_DELEGATED_READ_ENABLED=1` explicitly enables the route family. It defaults
off, requires its separately configured credential registry and enforces granular
read grants before projecting bounded results. Owner read credentials and named
write authority remain separate. No credential or grant is issued by importing
this implementation.

The versioned [contract fixtures](../contracts/devgraph-delegated-read/v1/README.md)
retain their original bytes. Carrier/Control integrations described by those
fixtures remain distinct from the generic Wallet write path. No deployment or
membership access rollout is implied by source tests.
