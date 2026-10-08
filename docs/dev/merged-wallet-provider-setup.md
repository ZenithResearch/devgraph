# Merged Wallet reconciliation

The generic native dependency and candidate source set pin Wallet main
`ff8de6ddc04ac2fb6b39408274276bc4ce984d42`. Its tree is
`379645255f3fec1b6359c25bfaa312fb38a703e7`, identical to the candidate qualified in
[Wallet Buildkite #28](https://buildkite.com/bananawalnut/castalia-wallet/builds/28).
The generic presentation crate is unchanged from the previous `2c53e9b` pin.
Request bytes, credential/presentation domains, native trust schema, and canonical
Todo/Work/Arena results are unchanged. Browser setup changes deliberately.

Wallet's Devgraph-specific adapter removal is merged. The remaining old decoder
dependency in Devgraph is frozen compatibility code, not a candidate Wallet or
fallback signer. Devgraph/secS removal and installed qualification remain gated.

## Operator-controlled browser setup

Inside the disposable qualification guest, prepare owner-private
`secrets/secs-magik/devgraph.work.v2/wallet-provider.json` under Devgraph's data root.
The file contains **public configuration only**, with this shape (replace the
illustrative key and IDs with the guest's explicitly provisioned public values):

```json
{
  "schema": "castalia.provider-profile.v1",
  "display_name": "Devgraph local authority",
  "origins": ["http://127.0.0.1:8080"],
  "membership": null,
  "presentations": [{
    "issuer": "secs://devgraph-work",
    "key_id": "guest-verifier-key-id",
    "public_key": "<64 lowercase hexadecimal characters from the guest verifier>",
    "audience": "devgraph://receiver-local",
    "callers": [{"kind": "browser", "id": "http://127.0.0.1:8080"}]
  }]
}
```

Use mode `0600`, with the existing owner-only receiver directories. The issuer
must exactly match the guest secS `authority/credential-presentation-v2.json`
issuer, and the key ID/public key must match its signer and the receiver's active
public registry. The origin must be explicitly admitted in `browser.json` and in
secS's caller configuration. This first setup supports one exact origin per
operator profile; it never broadens a proposal to other origins.

`POST /credential-work/v2/provider-profile` takes `{}` with the existing same-origin
admission rules. It rereads the owner profile and current managed receiver bundle,
rejects expired/revoked/unmatched keys, and returns only the strict public shape
with `Cache-Control: no-store`. It accepts neither bearer authority nor page-supplied
pins. It does not invoke secS, issue credentials, mutate grants, or approve trust.
The profile is a proposal; the operator must verify its displayed issuer/key
against the separately provisioned guest setup before approving it in Wallet.

In Kanban:

1. **Load Wallet setup** checks support and fetches public configuration.
2. **Review provider in Wallet** opens Wallet's own approval for request-credential
   trust. No membership request is included.
3. **Connect Wallet for moves** separately approves this origin's connection.
4. Review each requested change in Wallet. secS authorizes the exact resources;
   Devgraph checks the lifecycle, evidence, version and receipt at commit.

Each consent control invokes Wallet directly from its own click. Do not chain
provider approval to connection after an asynchronous wait: Wallet requires fresh
user activation. The SDK exposes this same sequence through
`client.prepareWalletSetup()` followed by separate `setup.approve()` and
`setup.connect()` click handlers. Missing capabilities produce an upgrade error;
missing operator configuration produces a setup error. Reads remain independent.

The new profile endpoint is not required for already configured generic clients;
the existing capabilities/preparation/signing transport remains compatible.
Reload setup after a key rotation, then approve the new public profile explicitly.
Wallet invalidates pending ceremonies when trust changes; the receiver continues
to reload policy/key admission for every execution. No renewal expands grants.

## Terminal and qualification

The native signer remains `castalia-wallet-present-credential-v2`, built from
Wallet's `castalia-wallet-cli` crate. It independently reads the owner-private
`CastaliaWallet/trust/credential-presentation-v2.json` in the actual OS user's
Zenith application-support directory. Keep `castalia.wallet-presentation-trust.v1`
and its exact terminal caller/pins; a browser profile is not a terminal trust file.
Approval still requires `/dev/tty`. No unattended or old-signer fallback is added.

Source checks use the merged Wallet's actual TypeScript profile parser via
`node scripts/qualification/wallet_provider_contract.mjs /path/to/wallet-repository`.
The script extracts the pinned Git blob without trusting checkout modifications,
compares the receiver's synthetic public fixture, and records a source-only report.
The SDK CI no longer attempts to build removed Devgraph-specific Wallet modules.

Installed acceptance requires the unchanged canonical extension package plus
matched native Wallet/secS binaries, Devgraph wheel and SDK package in a clean
macOS guest. The [release gate](generic-wallet-qualification.md) includes runtime
provider setup, both real signing paths, and their failure/recovery cases. No VM
runner is configured for this task. Source checks do not clear that gate. Live
installation, trust, credentials, grants, migrations and data remain unchanged.

## Historical source verification (2026-10-06)

| Check | Result |
|---|---|
| `bash docs/dev/verification.md` | 2,621 Python tests passed, 5 skipped; generated artifacts and Ruff passed |
| Frontend Node suites | 232 passed; affected setup/Kanban tests rerun after the final lifecycle check |
| Archive workspace at the historical import revision | 68 passed against the merged Wallet dependency; rerun public and native workspaces after transfer |
| Clippy / WASM SDK build | Passed |
| SDK unit tests and TypeScript | 25 passed; typecheck passed |
| Chromium, Firefox, WebKit | Core fixtures and separate-click setup fixtures passed in all three engines; synthetic provider only |
| Packed external consumer | SSR/ESM, strict TypeScript including setup controls, Vite, all three browser engines passed |
| Merged Wallet producer / Devgraph consumer | Exact pinned provider parser and receiver profile fixture passed |
| secS companion | 7 generic authority tests with the merged Wallet-generated synthetic signature fixture; native CLI source ceremony test passed |
| Public Devgraph documentation | 59 passed |
| Installed guest preflight | Blocked: physical Mac; no disposable macOS VM configured |
| Live activation | Not performed |

The independent signature fixture was generated from the merged Wallet Git
dependency, not inherited from an earlier qualification report. Native fixture
composition and browser gesture tests remain source checks; they do not exercise
the actual extension, interactive signer, secS and persistent receiver together.
The exact-artifact installed gate still requires every case, including the new
`provider-runtime-setup` case, to pass in the guest.
