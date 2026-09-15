# CLI authentication setup

## Named Work grant administration

Identity setup and permission grants are separate. After the signer is verified,
prepare a public plan and activate that exact private plan file:

```sh
devgraph auth work plan --output-file /absolute/private/work-grant.json
devgraph auth work apply --plan-file /absolute/private/work-grant.json
devgraph auth work status
```

The default plan covers all eleven named Work operations for the selected
signer using 41 explicit operation/resource-prefix rules. It includes Decision
resources only when acceptance/conversion need them. Default validity is 720
hours; `--ttl-hours` selects a bounded explicit duration. Review the plan's
actor, scopes and expiration before applying it. Existing operator authorization
to complete this exact setup is sufficient; ordinary reads never apply grants.

Native secS creates a verifier identity separate from the Wallet key and owns
its replay ledger. Devgraph installs matching receiver pins only after native
validation. No component renews an expired grant automatically. Use:

```sh
devgraph auth work renew --ttl-hours 720
devgraph auth work rotate-verifier --ttl-hours 720
devgraph auth work revoke
```

Renewal preserves the issuer and replay history while advancing policy version;
verifier rotation changes the service signing key and corresponding trust.
Revocation removes receiver trust first so outstanding signed projections are
denied. `auth work status` validates current producer/receiver agreement;
`auth status` retains the older bounded bundle-presence diagnostics.

For recovery copies and their custody limits, see
[local production maintenance](local-production-maintenance.md). Restoring a
backup does not renew authority or reverse a later revocation.

## Signer identity

For a **new identity**, create its keypair through the installed native Castalia
Wallet helper and save the reference in one explicit command:

```sh
devgraph auth key create
devgraph auth status --check
```

The default key file is the OS account's home directory followed by
`Library/Application Support/Zenith/CastaliaWallet/keys/devgraph-dregg.key`.
Wallet generates 32 bytes using the operating system CSPRNG and derives the
Ed25519 public key. This is the raw seed format accepted by Dregg; the named
Work v1 signer uses its Ed25519 half and makes no hybrid/PQ authorization claim.
The key file is mode 0600 in a private mode-0700 directory. This is file-based
custody, without vault encryption or Keychain storage. Devgraph receives only
the public descriptor and saves the key path/public pin. It never generates,
reads, or prints seed bytes itself.

Here “Wallet” means the local native program
`Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-work-v1`
under the account home. These CLI modes run without a browser or popup.

Creation refuses an existing signer profile, any explicit signer environment
override, or an existing key at the selected path. There is no overwrite flag.
It does not infer that a missing key means one should be created. For a custom
location, pass `--key-file /absolute/private/new-identity.key`; its private parent
must already exist and may not be a symlink. Key custody cannot use the profile
directory, which prevents a subsequent profile save from replacing the key.

To obtain an existing key's public pin without changing the selected profile:

```sh
devgraph auth key inspect --key-file /absolute/private/existing-identity.key
```

Generation and profile persistence are separate durable steps. If a key is
created but setup fails, the key is preserved and the command returns a nonzero
exit status. Inspect that key and use `auth setup` to finish; do not delete it
or generate a replacement to retry an uncertain outcome. A lost command response
is handled the same way. Inspection returns only the public key and file reference.
Creating a new key does not restore an existing Dregg identity, enroll membership,
or grant Work permissions.

Configure the existing Dregg identity once for the current macOS account:

```sh
devgraph auth setup \
  --key-file /absolute/path/to/existing/dregg-identity.key \
  --public-key <expected-64-character-lowercase-public-key>
devgraph auth status --check
```

Replace the example path and public pin with the existing identity's values.
Wallet opens the private key and verifies its Ed25519 public key before Devgraph
saves the reference. Setup does not sign a Work request, contact secS, mutate
the graph, generate a new identity, or create a Work grant. The current Wallet
adapter accepts an existing raw 32-byte Dregg seed file. Encrypted browser custody,
`.castalia-recovery`, `.castaway`, PEM, and hex seed text need their own custody
adapter; setup does not export or convert them.

The reference is saved at the operating-system account's home directory under
`Library/Application Support/Zenith/Devgraph/auth/signer.json`. The private
directory is mode 0700 and the file is mode 0600. The closed persisted schema is:

```json
{
  "schema": "devgraph.signer-reference.v1",
  "key_file": "/absolute/path/to/existing/dregg-identity.key",
  "public_key": "<expected-64-character-lowercase-public-key>"
}
```

Only the reference and public pin are stored. Paths are opened through held,
validated directory descriptors without following symlinks. Files with broad
permissions, unexpected ownership, hard links, extended ACLs, unknown fields,
duplicates, or oversized content are rejected. A private exclusive lock protects
updates; writes use a temporary file, fsync, and atomic replacement.

`devgraph work ...` and the legacy `agent-issue-create-v1` command automatically
use this saved profile. Only the Wallet child receives the two signer settings.
The secS child and HTTP requests never receive the private-key reference.
Codex and Hermes running as this same account can use the same profile; they
authenticate as the same principal when they select the same identity.

Existing trusted launchers remain compatible: a complete explicit pair of
`DEVGRAPH_SIGNING_KEY_FILE` and `DEVGRAPH_SIGNING_PUBLIC_KEY` takes precedence over
the saved profile. A partial or empty pair fails rather than mixing identities.
No `.env` is automatically sourced. Setup reports when an environment override
is present, so a saved change is not mistaken for the selected signer.

Use `auth setup --replace` with the same required arguments to replace an
existing profile. Use `devgraph auth forget` to remove the saved reference.
Neither command deletes the key or changes secS permissions. Forgetting does
not disable an explicit environment override.

## Status and credentials

`devgraph auth status` shows the selected signer reference, read-credential
validity, storage availability, and named Work producer/receiver bundle presence.
It does not open the private key. Add `--check` to ask Wallet to verify the key
and pin now; this exits 1 when the identity is missing or cannot be verified.
Ordinary status exits 0 when diagnostics were produced, including missing setup.
A successful identity check proves key possession only, not service readiness
or Work permission. Bundle presence is reported as `present_unverified` and
grant validity as `not_evaluated`; secS and Devgraph enforce the exact request,
operation, resources, policy pins, and validity window at execution time.

Create the existing local read-only capability through the same CLI namespace:

```sh
devgraph auth read provision
devgraph auth read rotate --ttl-hours 720
```

Provision preserves an existing valid credential. Rotation explicitly replaces
it and invalidates its previous value. Both print metadata only and preserve the
existing credential format, storage paths, and `devgraph.read` scope. The original
`devgraph local read-credential ...` commands remain supported. These commands
require available, owner-controlled configured storage. The read credential
does not authorize writes.

Named Work grants are still issued and enforced by secS. This setup flow does
not mint a writable bearer, bypass secS, broaden the expired Issue grant, or
claim that all Work operations are authorized just because a signer is saved.

## Compatibility

This is an additive local CLI feature. Work request, Wallet presentation, secS
projection, stored graph and receipt schemas are unchanged. The named Wallet
binary adds an exclusive `--check-identity` mode returning only
`{"ok":true,"identity_verified":true}` on success. An older Wallet rejects this
mode, so setup fails without writing a profile. Install the new Wallet binary
before enabling this CLI feature. Existing signing modes and their vectors are
unchanged. Older Devgraph CLIs continue to require the explicit environment pair.

The additional exclusive Wallet modes are `--create-identity --key-file FILE`
and `--inspect-identity --key-file FILE`. Their stdout is a closed public-only
descriptor with `schema: "devgraph.wallet-identity.v1"`, a 64-character lowercase
`public_key`, and `created: true` or `false` respectively. Errors contain fixed
codes on stderr. Mixed signing/creation/inspection flags fail before key creation.
The CLI accepts at most 1,024 descriptor bytes, rejects unknown/duplicate fields,
and never forwards raw child output. Older Wallet binaries fail closed for these
modes. Existing seed, profile, encrypted browser-custody, and signature schemas
retain their meaning.


Grant command exit codes are suitable for automation: plans return zero when
valid; status, apply, renew and verifier rotation require active matching grants
for zero. Revocation returns zero only when both receiver admission and producer
authority are revoked; partial receiver-only revocation returns nonzero with its
safe outcome retained. Verifier rotation changes the secS key, not the Wallet
actor. Rebinding to a different actor requires a separate explicit workflow.
