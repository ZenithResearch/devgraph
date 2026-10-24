# Secondary HTTPS owner RPC v3

The fixed `secs-local-admin-rpc-v3` binary and protected
`secs.local-admin-config.v3` record enable same-machine owner administration of
an explicitly published HTTPS interface, including a Tailnet DNS origin. They
open no HTTP operator route or new network listener. The protected host record
selects the graph, service instance, receiver, issuer, complete profile digest,
interpreter and immutable source inventory. Remote request data cannot choose a
program, runtime path, key or service configuration command.

V1 remains loopback-only and v2 remains private IPv4. Request/response v1 wire
schemas and inspect/challenge/execute operations are unchanged. The native v3
binary admits canonical HTTPS logical receivers; source/issuer/receiver/instance
bindings and the existing Wallet signature transcript remain mandatory. Every
command rechecks the registered service and public profile. V3 rejects an
unpublished v9 runtime; its v10 endpoint must retain the original active signed
owner and exact ceiling except for the explicitly configured receiver.

The configuration lives at the fixed account-home
`.zenith/rpc/v1/<instance>.json`; advisory service metadata lives separately at
`.zenith/services/v1/<instance>.json`. Publishing metadata never grants access.
The original resource registration, owner proof, legacy native binaries and
existing grants remain unchanged. A real owner reviews the exact requested
scope and signs in Wallet. The holder then accepts its credential in Wallet.
An inspection result or pending request is not evidence of credential receipt.

The matched Rust workspace uses one total deadline for stdin/stdout/handler
completion, including inherited descendant pipes, and kills the isolated handler
process group on failure. Unknown outcomes retain the original action identity
for reconciliation. No new decision is submitted automatically.

Source tests cover registered-profile changes/removal, wrong receiver/issuer/
resource/instance/digest, unpublished-parent rejection, signature replay and
unchanged legacy contracts. Installed qualification additionally checks the real
registered owner/native policies, fixed binary and source digests, and rejects
an unauthorized Wallet. Real unlock/approval is an explicit acceptance gate.

Current local installation evidence belongs in the private deployment workspace;
no machine paths, real keys, live service addresses or owner proofs belong in
public documentation or fixtures. The browser uses its own Wallet provider;
Transfer's owner Wallet may be a different identity from the requesting holder.
