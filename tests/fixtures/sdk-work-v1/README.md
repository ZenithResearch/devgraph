# Frozen SDK Work v1 fixtures

These synthetic fixtures are byte-exact copies retained from private Devgraph
`21b2db12c0609627f747740ea19f8e86b4d4647e`, as consumed by SDK source
`4d69a5f1eb7e4c02acb67d3e509eff001e2af006`. The Python parity test checks
requests and signed vectors against the existing receiver fixtures. The SDK
consumes the public protocol at its immutable Cargo pin; no local protocol fork
is shipped. Arena remains available through its existing receiver route, while
the SDK typed mutation/result envelope remains Work-only.
