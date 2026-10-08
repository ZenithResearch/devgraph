# Native bridge developer preview

This source component implements `org.devgraph.wallet_bridge` for the reviewed
Wallet extension. It is not installed by the build or SDK package. The fixed
receiver is `http://127.0.0.1:8080`, and the authority audience is
`devgraph://receiver-local`. The matching page and extension contracts are in
[`bridge-v1.md`](../../docs/sdk/bridge-v1.md).

Build with `cargo build --locked -p devgraph-browser-host --release`. An operator
must review and provision the two templates in `install/` before registering a
native messaging host with Chrome. Replace every placeholder, use the actual
approved Wallet extension ID in both files, and place `profile.json` beside the
installed binary. The native host independently checks the Chrome-supplied
extension origin against the private profile. For user-level Chrome registration,
macOS reads the reviewed native messaging manifest from
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/org.devgraph.wallet_bridge.json`;
Linux uses `~/.config/google-chrome/NativeMessagingHosts/org.devgraph.wallet_bridge.json`.
The manifest points to the absolute installed executable path, while the adjacent
private profile holds the matching extension ID and existing authority locations.
Only the extension grants page
consent; no page can select paths, URLs, process arguments, keys, or headers.

The installation directory and secS data root must be owned by the effective
user with mode `0700`; the profile and read credential must be private regular
files with mode `0600`, and the native issuer must be a private executable
(`0700`). Shared secS descriptor-relative private-file checks reject symlinks,
hard links, unsafe ancestors, permissive ACLs, and ownership-disabled mounts.
The browser host selects the adjacent `profile.json` by default; the optional
`--profile PATH chrome-extension://ID/` process arguments are for an operator's
local invocation, never browser messages.

The secS root must exactly match the effective user's passwd home plus
`Library/Application Support/Zenith/secS` on macOS or `.local/share/Zenith/secS`
on Linux. `HOME`, environment overrides, and a different private root do not
change this. The production issuer independently uses the same passwd-root
rule. A mismatch fails with `unsupported_profile` before any secS state is read
or an issuer is spawned. Isolated end-to-end qualification therefore needs a
separate OS user or VM with its own installed keys, grants, and port 8080.

This host consumes an existing read capability and existing reviewed secS
issuer. It cannot initialize service keys, grant authority, fall back to a native
Wallet signer, or add write bearer credentials. A fresh Wallet presentation is
verified by the shared Rust library, passed to the configured secS executable
using private temporary files, and its returned projection is verified against
current public registry and policy state. The opaque authorization is consumed
once and checked again immediately before the guarded HTTP mutation.

Native frames are bounded to 128 KiB before allocation. Each port retains at
most 65,536 unique control IDs and cancellation tombstones, at most 131,072
dispatched control/stream IDs for conservative late cancellation, and permits at most
64 active control tasks; saturation closes the port and cancels pending work.
There are four read slots, one mutation slot, and one outstanding pull per
stream. Pull chunks are at most 49,152 bytes. HTTP metadata is checked before
consumption, error bodies are capped at 64 KiB, and each response has an active
30-second whole-consumption deadline that drops idle readers and releases their
slots. The HTTP client disables proxies, redirects, cookies, and decompression.
Cancellation shares one atomic dispatch boundary with execution: a false
`dispatched` answer excludes later mutation dispatch; a true answer requires
outcome reconciliation and never implies failure or success.

`cargo test --locked -p devgraph-browser-host -p devgraph-client-native` covers
strict frames, control floods, replay IDs, cancellation races, idle stream
expiry, private profiles, shared Wallet signatures, synthetic issuer timeout and
termination, metadata refusal, and bounded responses larger than one MiB.
Synthetic fixtures never initialize or inspect the current user's secS state.
These tests do not establish installed-browser/real-receiver qualification;
that qualification remains a separate release gate.
