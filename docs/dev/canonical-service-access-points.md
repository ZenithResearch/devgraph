# Canonical service and access points

This source candidate adds opt-in routing over one existing, owner-reviewed
canonical provider composition. It changes no grant scope, membership rule,
owner identity, signing key, certificate or onward-delegation policy.

The canonical identity is the issuer/public key and `canonical_service_id`.
An HTTPS origin is an access point. A finite issuer-signed
`secs.service-access-point.v1` record binds that origin to `/service/v1` and the
provider's stable `authority_receiver`. TLS verification, explicit browser-site
consent and protected local registration remain separate requirements.

`canonical_access_points: true` is an additive runtime option requiring the
matched native holder-grant binary. `holder_grant_provider_runtime_file` selects
one existing provider; ownership, eligibility, key and binary pins must match and
provider chaining is rejected. The prefixed route composes that exact provider's
inbox, assignments, operator, enrollment and replay stores without an HTTP hop.
No signed body, challenge receiver, request ID or idempotency key is rewritten.
The member/public boundary applies the same route/method/body/abuse limits inside
the namespace and continues denying private graph and operator paths.

Local secS RPC retains the selected instance/profile/receiver envelope. Public
`inspect` can return a signed route binding. The additive `service_challenge`
and `service_execute` operations authenticate that binding using the protected
service-profile key, then invoke the same canonical operator. Local access never
creates an administrator exception or substitutes another Wallet's authority.

## Stored data and rollout

Legacy origin routes remain available for original pending-action recovery.
Native clients recover a retained legacy action on its original route. Existing
canonical-provider journals keep their original namespace; verified aliases use
that same namespace. Saved reusable grants already use issuer/key/service identity.
No SQLite stores are concatenated and no signatures or archived records are
relabeled. Conflicting legacy assignments require an explicit owner decision,
not an implicit migration or broader grant.

Older configurations omit the new flag and behave unchanged. New clients use
legacy negotiation only when the service explicitly lacks the new protocol;
invalid signatures, expired bindings, changed protected registration and failed
canonical negotiation fail closed. Stored expired public bindings remain readable,
but cannot authorize dispatch until refreshed.

Source tests and built app artifacts are not production activation. Deployment
must pin the new binary/source inventory, retain legacy stores, and review the
canonical provider selected by every admitted access point. Installed-device
qualification still requires the real Wallet and current provider authority.
