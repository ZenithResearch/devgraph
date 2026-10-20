# Standing grant validity v1

This application-neutral value describes the time bounds of a standing grant.
It does not attest that a grant exists, remains unrevoked, has a particular scope,
or permits credential issuance. The authoritative server checks all of those
conditions at issuance and at access. This value is not a credential or a
holder-created delegation protocol.

## Wire contract

Dated:

```json
{"schema":"castalia.grant-validity.v1","mode":"dated","not_before":100,"not_after":200}
```

Until revoked:

```json
{"schema":"castalia.grant-validity.v1","mode":"until_revoked","not_before":100}
```

The shape is closed. Every listed field is required; until-revoked values must
omit `not_after` (including null). A consumer must reject unknown versions,
unknown modes, unknown fields, duplicate keys after JSON unescaping, floats,
booleans, negative or unsafe integers, inverted and empty dated intervals.
Timestamps are Unix seconds in 0..9007199254740991. Dated `not_after` is strictly
greater than `not_before`. A standalone JSON object is at most 1024 UTF-8 bytes.

Time membership is `not_before <= now < not_after` for dated grants and
`not_before <= now` for until-revoked grants. “Until revoked” has no encoded
infinity timestamp. The server's current revocation, identity, installation,
scope and policy checks still apply; a client cannot assert their result.

## Attenuation

A child's start must be no earlier than its parent's start. A dated parent can
only produce a dated child ending no later than the parent's end. An
until-revoked parent may produce either kind. Time attenuation is necessary but
not sufficient: operation, resource, holder, receiver, issuer and policy
attenuation are separately enforced. No union may form an operation/resource
Cartesian product.

## Short credentials

Access credentials remain finite. An active grant can authorize a deadline no
later than `now + allowed_credential_lifetime`, clamped to its dated end if any.
The shared helper accepts lifetimes 1..3600 seconds; application policies may
impose a stricter minimum. Reject an inactive grant, invalid clock/lifetime or
unsafe computed deadline. Clamp before rejecting overflow: a dated grant ending
at the safe-integer limit may still issue a shorter remaining interval.

Credential renewal under an unchanged, active standing grant does not require a
new operator grant decision. Wallet disclosure/consent and any proof/challenge
expiry remain separate. Narrowing or revocation must stop existing credentials,
not only future issuance. This time value alone does not implement that registry.

## Compatibility and rollout

Historical Devgraph drafts v1..v4 and Castalia read credentials v1 keep their
original finite JSON and signature bytes. Finite adapters do not serialize this
new value into old objects. Old clients must never infer until-revoked from a
missing/null/distant timestamp. Accepting a standing grant with this value needs
an explicitly new enclosing draft, operator-action and persisted-policy version,
with matched producer/consumer activation. The first source slice supplied
strict Rust, Python and Swift readers, common vectors and finite adapters. The
opt-in [runtime v8 standing activation contract](standing-access-v8.md) now adds
new enclosing versions and durable registry handling as a source candidate.
Existing installations and grants are not automatically migrated or activated.

`grant-validity-v1.json` contains the shared cross-language vectors. Its cases
cover boundaries, safe-integer edges, duplicate and escaped keys, omitted/null
fields, mixed modes, overflow and monotonic attenuation. No real key, credential,
Wallet identity or installation data appears in these fixtures.
