# Delegated Devgraph reads

The opt-in delegated API is maintained in public `ZenithResearch/devgraph`.
See [integration status](dev/delegated-reconciliation.md) and the immutable
[v1 contract](contracts/devgraph-delegated-read/v1/README.md).

Delegated read grants select allowed operations, Work resources and Arenas.
Filtering happens before pagination. Expired, revoked, mismatched or insufficient
credentials fail closed. A read grant cannot authorize a Work mutation.

The generic Wallet write flow uses a distinct issuer-bound request credential,
explicit approval and secS resource authorization. See
[application-owned transport](dev/credential-presentation-v2.md).

The archive's earlier workstation and branch plans are historical only. No public
service, membership enrollment or live grant installation is included here.
