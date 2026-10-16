# Devgraph credential boundaries

The active implementation is in public `ZenithResearch/devgraph`. Use
[generic credential transport](dev/credential-presentation-v2.md),
[provider setup](dev/merged-wallet-provider-setup.md) and
[native setup](dev/native-generic-wallet.md) for the replacement integration.

Wallet confirms an issuer-bound disclosure; secS authorizes the exact operation
and resources; Devgraph validates current versions, workflow/evidence rules and
receipt consistency before committing. Provider trust and connection consent do
not install or expand resource grants.

Browser reads explicitly supply an in-memory read credential to Devgraph. Native
reads retain owner-private credential access. The optional delegated read contract
is separate: see [its fixtures](contracts/devgraph-delegated-read/v1/README.md).

Provisioning and installed acceptance use a disposable macOS guest with independent
OS identity. This source change creates no live trust, identity or grant. Earlier
archive branch/workstation inventories are not deployment instructions.
