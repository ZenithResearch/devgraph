# Zenith local space

`.zenith` is Zenith's own per-user coordination namespace on a machine. The
default path is `~/.zenith`; it is not nested beneath a Castalia namespace.
Castalia retains Wallet identity/custody responsibilities. Registering a service
in the Zenith space does not move or transfer its identity, credentials or data.

## Concepts

| Class | Meaning |
| --- | --- |
| `ZenithLocalSpace` | The operating-system user's local Zenith coordination space. |
| `LocalResourceRegistry` | The advisory public service index at `resources/v1` inside that space. |
| `LocalResourceRegistration` | One bounded, typed public descriptor. |
| `ServiceInstance` | The configured service instance described by a registration. |

`ZenithLocalSpace` → `HAS_LOCAL_REGISTRY` → `LocalResourceRegistry` →
`HAS_LOCAL_REGISTRATION` → `LocalResourceRegistration` → `DESCRIBES_SERVICE` →
`ServiceInstance`.

The machine-readable profile is [zenith-local-space.json](zenith-local-space.json).
All four classes use `runtimeLabel: false`: they introduce no Neo4j labels,
constraints, Work kinds, mutations, migrations or new authority subjects.
The existing `zenith.local-resource.v1` filesystem contract implements the
advisory descriptor separately from graph storage.

## Local identity and persistence

A registration retains a lowercase service-instance UUID across launches and
endpoint changes. This UUID is independent of a Wallet public key, member
installation, graph Actor, TCP port and current process. The record describes a
configured service; it is not a process heartbeat. Multiple configured instances
can have separate records, and local applications discover records for their
own OS user. Other machines use explicit connection sharing or a separately
specified discovery transport.

DevGraph is the first typed resource adapter. Future Hub/secS adapters must
publish their own supported management contracts; indexing a port does not make
it manageable. See the implementation's `docs/dev/local-resource-discovery.md`.

## Privacy, ownership and authority

Only the resource registry's explicit public descriptors are discovery data.
The whole `.zenith` space is not public: existing configuration, private state,
Wallet-related databases and integration files must not be swept into discovery.
This vocabulary does not relocate existing native app storage, rewrite shared
configuration or define a general file-sharing interface.

Local registration is not an owner signature, issuer approval, operator
admission, membership or access grant. Apps review public issuer/TLS settings
before saving a connection; changed registry entries cannot silently replace
saved pins. Castalia performs Wallet custody and consent; secS verifies admitted
authority; DevGraph executes its protected graph operations. An owner-signed
resource descriptor and owner bootstrap require their own accepted contracts.
