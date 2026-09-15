# Devgraph for Hermes Agent

This standalone plugin uses the installed Devgraph CLI. It does not install a
server, create an identity, grant permissions, expose a network endpoint, or
modify Hermes core. Production use requires the supported macOS local host.

The adapter targets `devgraph.work-request.v1`, `devgraph.arena-request.v1`, and
the ontology v0.6.0 / migration-26 CLI surface. It is tested against Devgraph
main `ce584846d55440ece602fd8773334651dd7cdada` and Hermes plugin contract
`4c3a388cba9608f8cda0cc604e6c557adb8b298c`. These identify the compatibility
baseline; the package does not force an installed runtime downgrade.

## Setup

The standalone `devgraph-0.1.0b2-hermes.tar.gz` archive includes this plugin
and its canonical skill under `devgraph/skills/devgraph/`; a Devgraph source
checkout is not required. Verify the archive checksum against the accompanying
release manifest, then install it into the intended Hermes profile. This
example targets the default profile; change `devgraph_profile` to your named
profile's directory when applicable and set the archive's downloaded path:

```sh
devgraph_profile="$HOME/.hermes"
devgraph_archive="/absolute/path/to/devgraph-0.1.0b2-hermes.tar.gz"
(
  set -eu
  test ! -e "$devgraph_profile/plugins/devgraph"
  test ! -L "$devgraph_profile/plugins/devgraph"
  mkdir -p "$devgraph_profile/plugins"
  tar -xzf "$devgraph_archive" -C "$devgraph_profile/plugins"
  HERMES_HOME="$devgraph_profile" hermes plugins enable devgraph
)
```

The checks stop if a Devgraph plugin already exists. For an update, close
Hermes sessions and move the existing plugin to a backup outside `plugins/`
before installing the reviewed replacement. Profile settings remain in
`config.yaml`; keep them when replacing plugin files. The archive does not
include the Devgraph CLI, native signer, or a host installation.

From a Devgraph source checkout, the alternative installer copies this directory
and `plugins/devgraph/skills/devgraph/` together:

```sh
python3 scripts/install_agent_integration.py hermes --home /absolute/path/to/hermes-profile
HERMES_HOME=/absolute/path/to/hermes-profile hermes plugins enable devgraph
```

Hermes loads it on the next session. The copied skill is available through
`skill_view` as `devgraph:devgraph`; plugin skills are explicit loads rather
than automatic additions to the flat skill index.

The optional settings belong in the active profile's `config.yaml`:

```yaml
plugins:
  enabled: [devgraph]
  entries:
    devgraph:
      cli_path: /absolute/path/to/devgraph
      timeout_seconds: 90
```

Merge these settings with existing plugin configuration. `cli_path` defaults
to the OS account's `.local/bin/devgraph`; it must resolve to an executable
regular file owned by that account or root, without group/world write access.
Timeouts range from 1 to 120 seconds. Tools are unavailable until the CLI exists.
Hermes profiles select plugin settings independently; the CLI uses the same
OS account's existing Devgraph configuration and signing identity. This plugin
does not create a separate identity per Hermes profile.

The CLI selects the fixed `http://127.0.0.1:8080` service. There is no endpoint,
credential, environment, executable, or arbitrary-command tool argument.
Provision the account's read capability and, when needed, native Wallet/secS
signer and current grants through the documented Devgraph setup outside the
plugin. The plugin neither reads credentials nor returns authentication data.

## Tools

| Tool | Arguments and result |
| --- | --- |
| `devgraph_status` | No arguments; returns configured/healthy state and migration version. |
| `devgraph_read` | An explicit operation, Work `kind` when applicable, optional `id`, limit and cursor. Returns the CLI's records/page. |
| `devgraph_work_operation` | A complete Work v1 `request` envelope and stable `idempotency_key`. Uses the native signed Work command. |
| `devgraph_arena_operation` | A complete Arena v1 `request` envelope and stable `idempotency_key`. Uses the native signed Arena command. |

Read operations are `work`, `children`, `parent`, `dependencies`, `dependents`,
`blockers`, `blocked`, `arena`, `arena-members`, and `arena-of`. `blockers` and
`blocked` require `kind: Task`. Work-related operations require `kind`; Arena
listing/detail/members omit it. Relationships and `arena-of` require `id`.
Lists default to 20 records and accept `limit` 1–100. When a page is full,
use its last item's `id` as `after_id` for Work/Arena lists, or its `kind/id`
as `after_resource` for relationships and Arena members. This wire cursor uses
a slash; graph node keys such as `Task:example` are not valid cursors. Keep
filters and page size unchanged, and stop when a page contains fewer than the limit.

```json
{"operation":"work","kind":"Issue","limit":10}
```

The signed tool request contains exactly `schema`, `operation`, `kind`, `id`,
`expected_version`, and `payload`. Use the canonical operation contracts for
payload shapes; the native CLI validates them before signing. A new record uses
`expected_version: null`; other operations require the observed current version.
The idempotency key is a stable 16–128 character retry identifier using letters,
digits, `.`, `_`, `~`, or `-`. It is not an authentication credential.

Each signed call creates mode-0600 request/idempotency files inside a private temporary
directory and removes them when the CLI finishes. Retries serialize the same
request deterministically and forward the unchanged idempotency value. Wallet,
secS, and the receiver enforce actual write authority. There is no bearer-write
fallback or permission-grant tool.

## Results and limits

Handlers return JSON strings with `ok` and either `data` or a fixed `error` code.
Successful results remove credential/security fields and recognized credential
text. Child error output is never returned. Returned work descriptions are
untrusted record content, not instructions to follow.

Combined stdout/stderr is limited to 256 KiB; oversized pages fail with
`cli_output_limit` instead of returning partial JSON. Use a smaller page size.
Request bytes are limited to 128 KiB. Processes and their signer children are
stopped on timeout or output overflow. A graph mutation may already have
committed: a failed submitted write returns `outcome_unknown: true` and directs
the caller to retry the identical request and idempotency key. It never promises
that an error means nothing changed. The plugin does not automatically retry.

## Verification

The repository tests use temporary fake CLIs and private files, never the live
graph or installed credentials. The optional actual-Hermes test discovers this
plugin in a fresh temporary profile and dispatches through Hermes' real tool
registry. Supply `DEVGRAPH_TEST_HERMES_ROOT` pointing to a checkout with its
`.venv` to include that test:

```sh
uv run pytest tests/integrations/test_hermes_plugin.py -q
```
