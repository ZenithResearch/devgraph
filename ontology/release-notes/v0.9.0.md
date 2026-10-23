# Zenith local space v0.9.0

Adds ZenithLocalSpace, LocalResourceRegistry, LocalResourceRegistration and
ServiceInstance as descriptive vocabulary with runtimeLabel false. Defines
`.zenith` as its own per-user machine namespace and `resources/v1` as an advisory
public service registry. Instance IDs are independent of Wallet keys, endpoints
and process IDs. Registration conveys no ownership, trust or permissions.

No graph label, Work kind, operation, signature domain, grant format, migration
or existing app storage changes. Historical bundles remain immutable. See
zenith-local-space.md, zenith-local-space.json and compatibility.md.

Also adds descriptive public service identity vocabulary. Service endpoints,
membership issuer endpoints, receiver credential issuer endpoints and public
verification methods remain separate from service instances, operator Entities,
Wallet identities, encryption recipients and authority. The public profile
defines which fields may be published and which secret/custody fields are
forbidden. See public-service-identities.md and public-service-identities.json.
