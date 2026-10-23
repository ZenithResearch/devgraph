# Public service identities

This profile names public service routes and verification material without
turning discovery data into authority. It covers two distinct signing roles used
by a provider gateway:

- a **membership issuer endpoint** issues the provider-accepted membership
  credential contract;
- a **receiver credential issuer endpoint** verifies the required membership
  evidence and issues a short-lived credential bound to an exact request digest,
  holder key, caller, audience, nonce and expiry.

The receiver credential issuer is not the membership issuer. A provider may use
the same operator or deployment for both roles, but clients must evaluate each
role against its own contract and trust policy.

## Concepts

| Class | Meaning |
| --- | --- |
| `ServiceEndpoint` | One versioned HTTPS or explicit local-loopback route exposed by a `ServiceInstance`. |
| `CredentialIssuerEndpoint` | A `ServiceEndpoint` that issues one named credential contract. |
| `MembershipIssuerEndpoint` | A credential issuer endpoint for membership credentials. |
| `ReceiverCredentialIssuerEndpoint` | A credential issuer endpoint for transient request credentials used at a receiver boundary. |
| `PublicVerificationMethod` | Public signature-verification material: issuer id, key id, algorithm and public key. |

`ServiceInstance` → `EXPOSES_ENDPOINT` → `ServiceEndpoint` and
`CredentialIssuerEndpoint` → `USES_VERIFICATION_METHOD` →
`PublicVerificationMethod`.

All five classes use `runtimeLabel: false`. The profile adds no Neo4j label,
constraint, Work kind, HTTP mutation route or storage migration.

## Public record boundary

A public instance record may publish:

- the endpoint id, endpoint kind, canonical origin and exact route;
- contract/schema identifiers and protocol version;
- issuer id, key id, public-key algorithm and public key;
- intended audience and exact allowed caller origins when those are part of the
  public verification contract;
- environment, operational state, source revision, policy digest and a public
  deployment-evidence reference.

It must not contain a private signing or encryption key, recovery material,
seed, bearer/OIDC token, Vercel or cloud secret, owner-private filesystem path,
Wallet custody reference, secS verifier secret, capability body, session token,
or unreviewed private contact information.

The record is discovery and verification input. It does not prove that the
route is live, that the operator controls the advertised private key, that a
credential is trusted, that the caller has authority, or that a deployment
matches source. Those claims require current network checks, signature
verification, independently pinned trust policy and deployment evidence.

## Signing and encryption separation

The gateway receiver signing identity signs the bounded receiver-credential
transcript. The gateway encryption recipient uses a separate asymmetric key to
open an intake envelope. The signing public key and encryption public key may be
published together in a provider catalog, but they have different purposes and
must have different key ids and contract fields. An encryption recipient is not
an issuer, and successful decryption is not Service Management admission.

## Instance publication

Operational records change more often than ontology vocabulary. A provider
therefore publishes instances through a separately versioned provider catalog,
well-known document or registry projection, and may reference that public record
from Devgraph. Key rotation updates the instance record and its evidence; it does
not rewrite this ontology release.

Until Devgraph accepts a separate runtime directory contract, these classes are
vocabulary only. Use Work and evidence records to track implementation and
deployment. Do not create private Neo4j labels or direct database writes for
endpoint instances.
