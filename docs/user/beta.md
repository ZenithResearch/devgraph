# Beta setup

Devgraph's beta offers a synthetic monitor demo, a persistent local read-only
host, and signed Work/Arena operations when native prerequisites are installed.
Choose the level you need; agent packages do not create identities or grants.

| Mode | Prerequisites | Data and authority |
|---|---|---|
| Monitor demo | Python 3.10+, uv | Synthetic in-memory data; read-only fixture credential |
| Persistent local reads | macOS, Python, Neo4j Community 5.26.29, Java 21 | Your graph; per-user devgraph.read bearer |
| Signed Work/Arena writes | Persistent host plus compatible native Castalia Wallet and secS, selected identity, current grant | Explicit operations bound to resource IDs, versions, and idempotency |

The source and agent bundles do not contain Neo4j, Java, Wallet, or secS binaries.
Native prerequisites must be obtained and verified separately; see
[native prerequisites and pinned bundles](../runbooks/beta-native-prerequisites.md). The managed
launchd host is macOS-specific; the portable Python fixture does not imply a
managed Linux or Windows installation path.

## 1. Start with synthetic data

From the source directory:

```sh
uv sync --locked
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open [the demo](http://127.0.0.1:4174/) and paste
`fake-credential-monitor`. Its data is synthetic and resets when the process
stops. No existing graph or credential is used. Do not point the agent CLI at
this fixture: authenticated CLI reads use the persistent host on port 8080.

## 2. Install the CLI

From the reviewed source bundle or checkout:

```sh
uv tool install .
devgraph --help
devgraph ontology
```

Ensure uv's tool executable directory is on the PATH inherited by your agent.
`uv tool dir --bin` reports it. This installs the Python package and CLI; it
neither configures the database nor installs native signing components. For a
locked development environment, `uv sync --locked` plus `uv run devgraph ...`
is also supported; an agent adapter needs the absolute `.venv/bin/devgraph`
path or an appropriate PATH.

## 3. Configure a persistent local host

Install and verify Neo4j Community 5.26.29 and Java 21 following the
[macOS host runbook](../runbooks/local-macos-self-host.md). Choose a dedicated
existing data directory. For example, after replacing the two runtime paths:

```sh
mkdir -p "$HOME/Devgraph-Data"
devgraph local configure \
  --data-root "$HOME/Devgraph-Data" \
  --neo4j-home /absolute/path/to/neo4j-community-5.26.29 \
  --java-home /absolute/path/to/jdk-21
devgraph local start
devgraph local status
```

`configure` checks prerequisites before writing per-user configuration, creates
private runtime state and credentials under the chosen data root, and installs
user launch agents. `start` admits Neo4j, applies packaged migrations, and then
admits the API. The ordinary directory example does not require a mounted disk.
Use `--require-mounted-volume` only when deliberately storing data on a separate
mounted filesystem. Reconfiguration does not copy or migrate existing data.

`local status` must show service liveness, clean migration readiness, and denial
of protected reads without a credential. A status result is evidence for this
host only. Do not expose the loopback listeners as a public service.

## 4. Read your graph and connect the monitor

```sh
devgraph query work Initiative --limit 50
devgraph query arena --limit 50
devgraph local read-credential status
```

The CLI reads the configured bearer internally, without printing it. To connect
[the persistent monitor](http://127.0.0.1:8080/), run the following locally and
paste the value into the monitor's credential field:

```sh
devgraph local read-credential show
```

Keep that value out of chat, screenshots, source files, and plugin settings. It
has exactly `devgraph.read`; it cannot mutate Work or Arenas. The monitor reader
shows descriptions and attached supporting material. Local document previews
need explicitly configured narrow document roots; a source link alone does not
mean document text is available. See [supporting material](../supporting-material.md).

## 5. Enable signed operations when ready

Full named Work and Arena operations are implemented. Their authority path
requires the compatible native programs installed under the actual account home:

- `Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-work-v1`
- `Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1`

These fixed native interfaces are additional prerequisites, not binaries supplied
by the Python package or plugins. Arena writes require their Arena-compatible
contract as well as the runtime's migration 26. Do not enable Arena grants while
any consumer still uses the older contract.

Follow [identity setup](../runbooks/cli-auth-setup.md) to select an existing raw
Ed25519 identity or explicitly create a new one. Then verify possession:

```sh
devgraph auth status --check
```

The owner must review the principal, resource rules, and expiry before applying
an authority plan. For a new Work grant, use a private directory:

```sh
umask 077
mkdir -p "$HOME/.local/state/devgraph/setup"
devgraph auth work plan \
  --output-file "$HOME/.local/state/devgraph/setup/work-grant.json"
```

Inspect that exact plan before activation:

```sh
devgraph auth work apply \
  --plan-file "$HOME/.local/state/devgraph/setup/work-grant.json"
devgraph auth work status
```

The default profile has eleven Work operations. An existing Work grant stays
Work-only until an explicit `auth work plan --renew --include-arenas` plan is
reviewed and applied; the extended profile adds Arena operations. Ordinary
renewal preserves the selected profile. Grants expire rather than silently
renewing. An identity check and a read credential do not prove write authority.

With current authority, agents call `devgraph work ...` and `devgraph arena ...`
with the versioned JSON request and a stable idempotency key. Read current
versions first. After a timeout or lost response, preserve the same request,
principal, and key so a retry cannot create a second mutation. See the
[named Work contract](../named-work-v1.md) and
[Arena contract](../../ontology/arena-runtime.md) for exact envelopes.

## Credentials and operational records

| Material | Stored by |
|---|---|
| Read bearer | Owner-private file under the chosen data root |
| Read token digest/claims | Receiver registry under the same root |
| Signer file reference and public pin | Per-user Devgraph auth profile |
| Raw identity seed | Native Wallet's operator-selected private file |
| secS verifier key and replay ledger | Native secS private authority bundle |
| Receiver public trust pins | Configured Devgraph data root |

File modes 0700/0600 provide local permission boundaries, not encryption or
Keychain custody. Codex and Hermes selecting the same profile use the same
principal. Neither plugin contains credential bytes or configures a remote
endpoint. No component automatically loads `.env`.

`EventReceipt` remains the versioned wire/storage name for an unsigned local
operational record. It is committed atomically with the mutation; `pending`
refers to local outbox progress. It is not a signed receipt, independent proof
of attribution, or proof of delivery to another system. Wallet/secS signatures
authorize the operation and are a separate claim.

## Agent packages and release artifacts

See [Codex and Hermes installation](agent-integrations.md). A reviewed release
build uses a clean committed tree and an approved license:

```sh
python3 scripts/build_beta.py --output-dir /absolute/new/directory/outside-checkout
```

For an owner-only review while the project license is undecided, add
`--private-review`. This marks the bundles for private review and includes the
release hold; it does not grant redistribution rights. The default public
candidate mode still requires `LICENSE`.

The build produces source, Codex, and Hermes `.tar.gz` files, a
`release-manifest.json`, and `SHA256SUMS`. The source bundle has no Git history;
agent bundles contain the canonical skill and their client metadata/adapter.
Native binaries are not included in these three archives. The
[native bundle workflow](../runbooks/beta-native-prerequisites.md) packages pinned native
sources and, when supplied, compatible binaries separately. Verify the checksums against the release's
trusted distribution channel before extracting. Checksums detect corruption;
the checksum file itself is not a publisher signature.

Build scripts and source tests do not establish that all runtime prerequisites
were installed or that a public release exists. Current limitations include
macOS managed hosting, local-only authenticated CLI traffic, explicitly
provisioned signing authority, and separately evidenced backup/restore readiness.
