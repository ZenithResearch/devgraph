# Headless agent Issue creation with an existing Dregg identity

`devgraph agent-issue-create-v1` composes the Wallet native signer, secS DG-E1
producer, and the existing exact local Issue-create receiver. It requires no
interactive browser ceremony. It currently covers **Issue creation only**;
generic HTTP mutation routes and the read capability's permissions do not change.

## Required local resources

| Resource | Owner and purpose |
| --- | --- |
| Existing Dregg raw 32-byte identity file | Wallet alone reads the private bytes to sign. No key is generated or copied. |
| Expected Ed25519 public key | Operator pin confirming the selected existing identity. |
| `~/Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-issue-create-v1` | Fixed native Wallet signer executable. |
| `~/Library/Application Support/Zenith/secS/bin/secs-devgraph-issue-create-v1` | Fixed secS producer, using its own service key and installed policy. |
| `~/Library/Application Support/Zenith/secS/authority/devgraph.issue.create.v1/` | Existing secS manifest, service identity, trust registry, receiver policy, and replay database. |
| `<configured data root>/secrets/secs-magik/devgraph.issue.create.v1/` | Existing Devgraph receiver manifest and secS public-key registry. |

Binary paths derive from the effective user's account home. Callers cannot
select an executable, service URL, database, policy, operation, or audience.
Executables and their full ancestor paths use the existing local integrity
checks; installs must be regular, single-link, owner-controlled executables.

The trusted Codex/Hermes process launcher may export these values from a
gitignored `.env`:

```dotenv
DEVGRAPH_SIGNING_KEY_FILE=/absolute/path/to/the/existing/dregg-identity.key
DEVGRAPH_SIGNING_PUBLIC_KEY=<existing Ed25519 public key: 64 lowercase hex characters>
```

`.env` is not automatically discovered or sourced. The key setting is a path,
not raw key bytes. The file must be private, regular, single-link, ACL-free,
and reached without symlinks on an ownership-enforcing filesystem. The native
signer accepts only the existing Dregg raw 32-byte seed format; encrypted
Wallet recovery or browser custody needs a compatible adapter and must not be
exported as a plaintext seed for this command.

## Execute

Prepare private request and idempotency files. A minimal request is:

```json
{"id":"issue-example","kind":"Issue","title":"Example issue"}
```

The idempotency file must contain 16–128 ASCII characters from
`A–Z a–z 0–9 . _ ~ -`, followed by one LF. Retain that same key for retries
after an uncertain outcome.

```sh
devgraph agent-issue-create-v1 \
  --request-file /absolute/private/request.json \
  --idempotency-key-file /absolute/private/idempotency-key.txt
```

The command validates both fixed executables, snapshots both input files into
a private temporary directory, and uses those same snapshots throughout:

```text
Agent → Wallet native signer → secS producer → Devgraph exact receiver
          existing identity     installed grant   Issue + EventReceipt
```

Only Wallet receives the two signer environment settings. Child processes
receive no unrelated environment secrets, loader overrides, proxies, or shell
configuration. Each child has a 30-second timeout. Presentations and authority
files are removed when the command exits; the key file is never copied.
Successful output is the existing bounded receiver result, including duplicate
and receipt evidence. A pending receipt records a committed mutation with a
pending local outbox; it is not an external delivery confirmation.

## Denial and production acceptance

Wallet failure means request/custody validation failed: check the file reference,
public-key pin, format, and private permissions. secS failure means no acceptable
projection was produced: check installed service identity/trust and the exact
actor/resource grant's status and time window. Neither case invokes the
Devgraph receiver. No failure creates or renews a grant automatically.

Sharing the Dregg identity between Codex and Hermes makes them the same
authenticated principal; process names do not distinguish their signatures.
This v1 contract uses the Ed25519 half and makes no PQ authorization claim.

This is additive CLI composition with no wire, database, or policy migration.
The original `wallet-issue-create-v1` browser path remains available. Before
calling the new path live, verify the installed binaries, explicit existing
identity, current secS grant, matching receiver binding, and a real accepted
Issue/receipt followed by a protected read. Test-vector parity alone is not
live activation or full Work API coverage.
