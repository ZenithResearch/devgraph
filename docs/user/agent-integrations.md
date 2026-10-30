# Codex and Hermes integrations

Both integrations use the same skill from
`plugins/devgraph/skills/devgraph`. They operate through the Devgraph CLI and
its existing per-user configuration. Install the Python CLI and, for real data,
configure the local host as described in [beta setup](https://github.com/ZenithResearch/devgraph/blob/main/docs/user/beta.md) first.

A plugin install copies code and instructions. It does not install Neo4j,
create an identity, expose a service, copy a bearer, or grant write authority.
Use a fresh agent profile for a first integration check.

## Codex plugin

The source bundle and checkout include a repository-local marketplace at
`.agents/plugins/marketplace.json`. From that repository root:

```sh
codex plugin marketplace add "$PWD"
codex plugin add devgraph@devgraph-beta
codex plugin list
```

For the dedicated Codex archive, verify and extract it, change into the
`devgraph-codex` directory, and run the same commands. Keep the extracted source
available while using a local marketplace. These commands register this local
marketplace through the Codex CLI; no hand-edit of personal marketplace files
is needed. Start a new Codex task after installation so it loads the skill.

The plugin supplies a skill, not an MCP server. The task must have permission
to run the local `devgraph` CLI, and that executable must be on its inherited
PATH. Try: “Use Devgraph to list my active initiatives and their Arenas.”

### Standalone Codex skill

For a skill-only install, run this from the source root instead:

```sh
python3 scripts/install_agent_integration.py codex-skill --home "$HOME/.agents"
```

It copies the canonical skill to `~/.agents/skills/devgraph` and refuses to
replace an existing destination. It makes no Codex configuration changes.
Use either the plugin or the standalone skill to avoid duplicate instructions.
For an update, inspect the new package and move the old skill out of the skill
search directory before installing; the installer will not delete it for you.

## Hermes plugin

Use a Hermes version with the public standalone-plugin API. From the Devgraph
source root, install to the desired profile:

```sh
python3 scripts/install_agent_integration.py hermes
hermes plugins enable devgraph
```

The installer uses `HERMES_HOME` when set, otherwise `~/.hermes`. For an explicit
profile, keep the same value for installation and the Hermes process:

```sh
export HERMES_HOME="/absolute/path/to/hermes-profile"
python3 scripts/install_agent_integration.py hermes --home "$HERMES_HOME"
hermes plugins enable devgraph
```

The destination is `<Hermes home>/plugins/devgraph`. The installer refuses an
existing destination and copies the canonical shared skill into the plugin's
`skills/devgraph`; it does not enable the plugin or edit `config.yaml`.
The dedicated Hermes archive contains that assembled `devgraph` directory;
extract it into a new staging directory, inspect it, and move it into the selected
profile's `plugins` directory only when no existing `devgraph` destination exists.
Then explicitly enable it as above.

The plugin defaults to the account's `~/.local/bin/devgraph`. If your CLI is
elsewhere, merge this section into that profile's existing `config.yaml`, using
the verified absolute executable path; do not replace unrelated settings:

```yaml
plugins:
  entries:
    devgraph:
      cli_path: /absolute/path/to/devgraph
      timeout_seconds: 90
```

`cli_path` is a local executable path, not a URL or a credential. Timeout is
1–120 seconds. The plugin has no API URL, token, seed, or caller-selected config
field. Its tools are:

| Tool | Purpose |
|---|---|
| devgraph_status | Bounded CLI status and prerequisite diagnostics |
| devgraph_read | Bounded Work, relationship, and Arena reads |
| devgraph_work_operation | Signed named Work request with a stable idempotency key |
| devgraph_arena_operation | Signed Arena request with a stable idempotency key |

The shared skill is registered as `devgraph:devgraph`; load it with Hermes
`skill_view` when working on Devgraph. Plugin skills are explicit loads, not
automatic additions to the flat skill index. Start a new Hermes session so
registration takes effect. The
adapter uses the existing Devgraph CLI; it does not query Neo4j directly.

## Verify access in stages

`auth work status` reports the historical managed agent profile separately from
custom standing policies. A validated custom policy returns
`state: request_evaluation_required`, `ready: false`, and
`operations_authorized: 0` when the selected identity, receiver pins and service
key match and an allow rule is current. This means an exact request still needs
native secS evaluation; it does not mean the signer has no grant or that a request
is authorized. Deny rules, related resources and independent rule validity must
be evaluated together for each operation. Expired/missing admission or mismatched
pins remain inactive; malformed descriptors remain unavailable/unsafe.

Status inspection never converts a custom policy into a managed profile.
Managed renewal and provisioning continue to reject nonmatching profiles rather
than replacing scoped session rules. The native administration descriptor has a
separate 512 KiB bound because it contains the whole shared policy; private plan
and receiver-file limits remain 64 KiB.

### Generic Wallet candidate and agent approval

The supported replacement uses generic Wallet credentials. See
[candidate setup](../dev/merged-wallet-provider-setup.md) for matched source,
provider trust and the installed qualification gate. Use `--credential-v2`
explicitly only with that matched setup. Transport v2 does not rewrite the
Work/Arena v1 request or its signing domain; canonical Todo uses Work v2.

Generic terminal signing requires the person's real interactive approval.
An unattended agent receives `approval_required`: retain the private request
and idempotency files and hand off that exact command to the person. Do not
simulate a terminal confirmation, renew authority automatically, or fall back
to a retired signer. Preparation is not a committed graph mutation.

After unknown generic dispatch, use the original files with
`--credential-v2 --reconcile` and fresh human approval. Reconciliation only
checks receipt status; it does not resubmit the mutation. A missing receipt
remains unknown. Report success only after a validated committed receipt.

### Existing named-v1 compatibility installations

1. Confirm the agent finds the CLI. For ontology inspection, run
   `devgraph ontology --format sources-v1` and require the supported
   `devgraph.ontology-sources.v1` schema with `closureVerified: true`.
   Read returned installed paths, not repository paths. This capability first
   ships in 0.1.0b3; an older or absent CLI needs the supported CLI installation
   before this ontology workflow. Plain `devgraph ontology` retains runtime-v1.
2. Inspect `devgraph local status` and run a bounded `devgraph query work
   Initiative --limit 10` or `devgraph query arena --limit 10`.
3. For signed work, separately check `devgraph auth status --check` and
   `devgraph auth work status`. Missing native Wallet/secS or an inactive grant
   is a setup requirement, not a reason to fall back to a writable bearer.
4. Only after setup and an intended user operation, submit a named Work or Arena
   request with current versions and one stable idempotency key. Read the result.

A “list initiatives” smoke check performs no mutations. If a write times out,
its outcome is unknown; for the retained named-v1 compatibility path, repeat
only the identical request and key using the same identity. Use status-only
reconciliation for the generic path above. Never generate a fresh key to retry
an unknown outcome.

`wallet_request_incompatible` or `secs_request_incompatible` means a native
component rejected a request already validated by Devgraph. Check the matched
companion versions and operation support; changing keys or extending a grant
does not fix a parser mismatch. `wallet_custody_unavailable` identifies a key
reference/pin/permissions check, while `secs_authority_denied` identifies grant
or presentation rejection. The CLI retains only a bounded, recognized public
diagnostic and never prints arbitrary native stderr. These pre-dispatch errors
mean this invocation made no HTTP mutation request; they say nothing about any
earlier uncertain invocation.

Read credentials are loaded by the CLI; never put them into agent configuration,
a prompt, or a package. Codex and Hermes using the same signer profile act as
the same principal. Separate agent names do not create separate grants. The Hermes
adapter clears inherited signer environment overrides and uses the account's
saved Devgraph profile; it does not select an identity from plugin settings.

## Uninstall or update

For Codex, use `codex plugin remove devgraph@devgraph-beta`; the repository files
and Devgraph service data remain independent. Install the reviewed version from
the configured marketplace with `codex plugin add devgraph@devgraph-beta`, then
start a new task. For a standalone skill, move its installed directory out of
the skill search path before installing the next version.

For Hermes, use its plugin disable command before removing or replacing the
installed plugin directory. Preserve the old package for rollback and install
the new package into a fresh destination. Integration removal does not revoke
Devgraph grants or remove graph data; those are separate operator actions.

## Finish work in a tracked session

DevGraph's session work disposition is independent of an application's retained
conversation and current turn. Use the [session state workflow](../dev/session-provenance.md#finish-or-reopen-work-in-a-session)
to prepare an exact `session.state` update, then submit through the normal native
Wallet/secS approval path. Mark Done only when the intended work in that session
has finished; closing a turn or receiving a successful tool result is insufficient.
Reopen explicitly before treating that conversation as Open work again. A Done
session can still have unfinished linked tasks; each task keeps its own progress
and acceptance rules. State changes do not cascade to child or runtime sessions.
