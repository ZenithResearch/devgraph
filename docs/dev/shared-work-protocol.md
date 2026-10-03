# Shared Work and Arena request protocol

`devgraph-work-protocol` is a public, source-only Rust package for native and
WebAssembly callers. Its checked `WorkRequest` owns canonical bytes, the exact
operation name, a sorted/deduplicated resource inventory, and the request digest.
Callers cannot independently construct or edit derived fields. It has no custody,
clock, randomness, grant, replay, network, database, or execution capability.
The package is consumed through an immutable Git revision; `publish = false`
intentionally prevents a crates.io release.

## Compatibility

Existing Work v1 canonical bytes and `devgraph.work-request.v1\0` digests remain
unchanged. Arena v1 uses `devgraph.arena-request.v1\0` and closed create, patch,
archive and member.set operations. Member changes bind their Initiative/Task and
all non-null old/new Arena references. A Work Task parent change may bind its
previous Arena explicitly. Omitting that optional field preserves old Work bytes.
These request domains do not change any Wallet presentation signature domain.
A request and its resource set describe intended work; they grant no authority.

Rust rejects non-integer number spellings, lexical negative zero, duplicate and
unknown fields, unsafe integers, excessive nesting, oversized inputs and invalid
resource references. The adversarial corpus records the existing Python behavior
separately: Python accepts lexical negative zero as integer zero, while Rust's
portable grammar rejects it. This export preserves that documented asymmetry.
Unknown schemas remain rejected; no stored identity, recovery file, membership,
policy or runtime record is migrated by this package.

## Source provenance and disclosure scope

The Work parser and conformance cases were extracted as reviewed file contents
from source revision `21b2db12c0609627f747740ea19f8e86b4d4647e`.
Arena parsing/resource rules were ported from Castalia Wallet revision
`ef580170a7ad94c46243c3a490bed1aa6819b612` into this shared parser, using the
feature-independent canonical encoder. Both authored implementations were by
Gabriel Atkinson. This publication is rooted in public Devgraph history and
imports no private Git ancestry.

The exported fixtures contain 15 Work requests, 25 adversarial cases, eight
Arena/parent-change requests and 19 Arena denials. They use synthetic example
records, not live credentials or identity material. Work/Arena request copies
are checked against the existing public Python receiver fixtures. The export
excludes private signed projections/policies, delegated-access runtime code,
operator configuration and deployment material. Existing LICENSE and NOTICE
apply; the crate is AGPL-3.0-only.

## Verification

```sh
cargo test --locked -p devgraph-work-protocol
cargo test --locked -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr
cargo check --locked -p devgraph-work-protocol --target wasm32-unknown-unknown
cargo +1.85.1 test --locked -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr
uv run pytest -q tests/model/test_shared_work_protocol.py
```

Rust 1.96.0 is the pinned build toolchain; 1.85 is the package's supported minimum.
The feature matrix verifies canonical output under downstream serde feature
unification. Rust tests cover digest domains and all resources, not only JSON
acceptance. Python tests independently consume the same synthetic requests.
Consumers must separately verify their presentation, consent, custody and actual
WASM bindings against the pinned public revision. Passing these checks does not
qualify a deployed Wallet/secS/Devgraph stack or authorize Arena grants.
