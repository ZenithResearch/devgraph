# Zenith local space v0.9.0

Adds ZenithLocalSpace, LocalResourceRegistry, LocalResourceRegistration and
ServiceInstance as descriptive vocabulary with runtimeLabel false. Defines
`.zenith` as its own per-user machine namespace and `resources/v1` as an advisory
public service registry. Instance IDs are independent of Wallet keys, endpoints
and process IDs. Registration conveys no ownership, trust or permissions.

No graph label, Work kind, operation, signature domain, grant format, migration
or existing app storage changes. Historical bundles remain immutable. See
zenith-local-space.md, zenith-local-space.json and compatibility.md.
