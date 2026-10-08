# Shared Rust protocol compatibility

The protocol source is extracted from the reviewed native Wallet/secS named Work
parser. Devgraph native consumers and WASM use the canonical crate in this public
repository, built at the same source revision, with private derived request fields and recursively sorted signed JSON independent of Cargo
map-order feature unification. Existing signed v1 bytes and integer limits remain
unchanged. The Python receiver remains authoritative for execution.

The positive request/signature fixtures in `tests/fixtures/sdk-work-v1`
match the receiver fixtures byte-for-byte. The adversarial fixture records separate
Python and Rust decisions, canonical bytes, digests and resources. Both runtimes
execute it in tests; it is not a language-local serialization round trip.

| Difference | Decision |
| --- | --- |
| Literal JSON integer `-0` is accepted/canonicalized as zero by Python; serde_json dispatches it to the float visitor, rejected by the original Rust parser. | Preserve the Rust rejection during extraction. The browser builder may emit canonical integer zero for an ordinary valid zero value. Broadening raw-byte acceptance requires a separate reviewed compatibility correction. |

Mechanical extraction must not quietly admit floats, unsafe integers, duplicate
decoded keys or malformed Unicode to erase a discrepancy. The production request
decoder and the SDK's signed-64-bit response decoder are distinct contracts.
