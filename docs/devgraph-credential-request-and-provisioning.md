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

Standing read activation is documented in [v2](contracts/castalia-access-request/v2/README.md).
The opt-in source path for persistent scoped write activation and independent
native admission is [v3](contracts/castalia-access-request/v3/README.md).

## Monitor approval handoff

The monitor keeps the next Wallet action beside the selection being reviewed.
After preparing a decision, the original approval button is hidden and the
selection is frozen until Wallet finishes or the unsigned step is cancelled.
Opening Wallet still requires a separate user click; the monitor never approves
on the operator’s behalf.

An approval challenge lasts two minutes, independently of the standing grant’s
expiry. The monitor shows that timer and rejects an expired challenge before
calling Wallet. **Refresh this approval** requests a new challenge for the exact
same unsigned action and action ID, without dispatching a decision. A second
explicit click opens Wallet. Policy changes and stale reviews remain subject to
server validation; refreshing does not extend grants or update selections.

Unsigned cancellation is unavailable after uncertain dispatch or during status
recovery. Recovery retains the original action and checks its result only.
Existing signed bytes, approval domains and stored recovery records are unchanged.

The read connection distinguishes refreshing an expired review from opening
Wallet. It reports opening, issuer enrollment and Wallet credential acceptance
separately, and only announces a connection after acceptance. Bounded errors
distinguish a locked Wallet, untrusted issuer, invalid read policy, failed popup,
and another pending approval. No credential or request body is placed in those
diagnostics. A refreshed review still needs a new explicit Wallet-opening click.
