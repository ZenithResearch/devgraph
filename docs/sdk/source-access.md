# Source access and build boundaries

Public `ZenithResearch/devgraph` owns the Python application, canonical protocol,
web/native SDK source and receiver. The private Devgraph repository is a deprecated
archive. Builds never fetch it or depend on its branch/PR stack.

The root Cargo workspace contains only the protocol, core SDK and web/WASM crate.
Public verification resolves public registry dependencies without repository
credentials. The SDK uses the canonical protocol from the same checkout and records
that public commit in its package manifest.

Wallet remains a separate private repository. Native SDK and historical browser
host crates have independent manifests and lockfiles under `crates/`; their exact
Wallet/secS pins require authorized companion source access. No private Wallet
implementation or history is copied into this repository. Devgraph's license does
not grant rights to companion code.

The manually invoked companion job obtains a contents-read token for only
`bananawalnut/castalia-wallet`, using the configured source App. Its absence blocks
that CI job, not public protocol/web builds. The credential helper accepts only
that exact HTTPS repository path and never embeds a token in a URL or artifact.
This change does not configure an App, secret or permission on GitHub.

Source access and the installed qualification gate are separate. See
[implementation status](implementation-status.md) and
[installed qualification](../dev/generic-wallet-qualification.md).
