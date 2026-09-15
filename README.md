# Devgraph — local work graph, public beta

Devgraph is a local work graph for people and agents. It connects Proposals,
Initiatives, Projects, Issues, and Tasks with their descriptions, dependencies,
evidence, and plans. Arenas group ongoing areas of responsibility. The browser
monitor lets you explore the graph and read the records behind it.

The beta includes a Python API/CLI, a read-only browser monitor, project-selection
tools, a Codex plugin, and a Hermes adapter. It runs on your machine. Publishing
or cloning this source repository does not upload your local graph or credentials.

## Choose a starting point

| You want to… | Start here |
| --- | --- |
| Explore the graph, read plans, and understand what you see | [Guide for general users](docs/user/generalists.md) |
| Build an integration or use the API, CLI, and signed operations | [Programmer guide](docs/user/programmers.md) |
| Install a persistent local graph | [Beta setup](docs/user/beta.md) |
| Use Devgraph from Codex or Hermes | [Agent integrations](docs/user/agent-integrations.md) |

Try the synthetic demo first. The persistent host currently targets macOS;
Neo4j and Java are separate prerequisites. Writing to your graph also requires
compatible native Castalia Wallet/secS components, an explicitly selected
identity, and current authority grants. Those components are not bundled with
the Python package or agent plugins.
Native companion bundles are currently private and separately distributed;
signed-write setup requires access to those components. The public source alone
is sufficient for the synthetic demo and, with Neo4j/Java, persistent local reads.

## Try the monitor

With Python 3.10+ and `uv` installed, run these commands from an extracted source
bundle or checkout:

```sh
uv sync --locked
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open [the demo monitor](http://127.0.0.1:4174/) and enter
`fake-credential-monitor`. This is synthetic, in-memory data with a synthetic
read credential. Restarting discards it. It needs no Neo4j, identity, or grant;
its credential is never accepted by the persistent local host. Stop the demo
with Ctrl-C.

Select a node to open its details. **Description / plan** contains authored
text; **Load supporting material** resolves attached documents, links,
requirements, and criteria. Use **Fit**, zoom, **Expand view**, and the resize
handles to make room. The category checkboxes hide or show node classes.
**From Arena** follows outgoing connections, so it may include dependencies
belonging to another Arena; it does not move work between Arenas.

For your own persistent graph, follow [beta setup](docs/user/beta.md). Managed
local hosting currently targets macOS with Neo4j and Java. Signed writes also
require the separately installed compatible native Wallet/secS components and
an explicitly provisioned identity and authority grant.

## Connect an agent

[Agent integrations](docs/user/agent-integrations.md) covers both clients:

- **Codex:** install the bundled `devgraph` plugin from the repository-local
  marketplace, or install its standalone skill.
- **Hermes:** install the standalone plugin to the selected Hermes profile and
  explicitly enable it. It provides status, bounded reads, and signed Work/Arena
  tools through the same local CLI.

Both use the canonical skill at `plugins/devgraph/skills/devgraph`. They use your
per-user Devgraph configuration and existing authority; packages contain no
credentials or host-specific configuration.

## What is in the graph

- **Work:** Proposal, Initiative, Project, Issue, and Task, with typed lifecycle,
  parentage, dependencies, and version checks.
- **Arenas:** independent records grouping parentless Initiatives and Tasks;
  descendants inherit from their Work root. Dependencies do not confer membership.
- **Observations:** a scout's evidence-backed interpretation of an external
  repository or organization: the problem, desired outcome, and source links.
  They begin inferred and unclaimed. They are not maintainer-authored Initiatives
  or proof that a maintainer has committed to a plan.
- **Supporting material:** attached Artifact/ExternalLink metadata, requirements,
  criteria, and bounded text previews from explicitly configured document roots.
- **Operational records:** `EventReceipt` is the existing versioned API/storage
  name. These records are unsigned and committed atomically with mutations.
  Signed authorization is separate; a record is not a portable cryptographic
  receipt or proof of external delivery.

The monitor currently labels operational records **Receipt**. A pending record
means the graph mutation committed and local outbox processing has not advanced.
See the [general-user guide](docs/user/generalists.md#understand-observations-and-records)
for how to interpret these alongside ordinary work.

**Project selection** lets you enter outcome/effort estimates and compare a
dependency-aware scenario. Its inputs and saved drafts are local to the browser
tab; exports are local files. It does not update canonical Work or promise a
schedule or hard-budget solution. See [project selection](docs/user/project-selection.md).

Ontology bundles through `v0.6.0` are packaged with the application.
`devgraph ontology` identifies the canonical release. `Entity`, `Person`,
`Agent`, and `Organization` are directory vocabulary with no admitted runtime
CRUD; `Actor` remains a compatibility alias.

## Documentation

- [Guide for general users](docs/user/generalists.md)
- [Programmer guide](docs/user/programmers.md)
- [Beta installation, credentials, and limits](docs/user/beta.md)
- [Codex and Hermes installation](docs/user/agent-integrations.md)
- [Operator frontend](docs/user/operator-frontend.md)
- [Work concepts](docs/user/work-graph-concepts.md)
- [Named Work API](docs/named-work-v1.md) and [Arena contract](ontology/arena-runtime.md)
- [Supporting material](docs/supporting-material.md)
- [HTTP API](docs/api.md) and [bounded Cypher reads](docs/cypher-read-v1.md)
- [Local macOS host](docs/runbooks/local-macos-self-host.md)
- [Identity and grants](docs/runbooks/cli-auth-setup.md)
- [Current-state evidence and limits](docs/current-state.md)

This is a local beta. Repository tests do not establish public-hosting,
backup/restore, or production-readiness claims for another machine.

## Data and credentials

The persistent host stores your graph and operational state under the data root
you choose. The demo stores only process memory. Neither is a hosted sync service.
The monitor remembers its read credential in the current tab's `sessionStorage`;
the CLI loads the configured credential internally. Keep real credential values
out of prompts, screenshots, source files, and plugin settings.

Read access cannot authorize mutations. Wallet signs intended Work/Arena changes,
secS checks the applicable grants, and Devgraph checks the request and versions.
Local file permissions protect credential files; this is not a claim of encrypted
storage or Keychain custody. [Beta setup](docs/user/beta.md#credentials-and-operational-records)
explains what each component stores.

## Runtime and operations

The persistent service uses the private, loopback-only production composition
in `devgraph.runtime`; this beta is not a public or highly available service.
Its local production credential verifier admits the owner-provisioned
`devgraph.read` capability. The in-memory demo verifier is a separate fixture.
The bounded `devgraph` operator CLI exposes health, reads, local lifecycle, and
signed operations without a generic bearer-write or database bypass.

Neo4j operations tooling includes Forward-only migration, backup-artifact
creation, restore-preflight checks, and disposable database verification. These
operations require the appropriate operator-selected target and evidence; their
presence in the package does not verify another installation's recovery posture.

## Development checks

```sh
uv sync --locked
uv run pytest -q
bash docs/dev/verification.md
uv run ruff check src tests scripts integrations
git diff --check
```

Live Neo4j checks are opt-in and need a disposable target. The default suite
requires no live database. See the beta guide for reproducible source and agent
bundle builds; native binaries are distributed separately.

## Continuous integration contract

GitHub Actions runs on pull requests to `main` and pushes to `main`, using the
locked Python environment for tests, repository verification, lint, whitespace,
and Python/source/agent package builds. Build artifacts and passing tests do not
establish native-companion availability or acceptance on another machine.
Passing CI proves repository behavior, not deployment, live Neo4j,
Matrix, secS-magik, Dregg, Hermes runtime, or any other external integration.
Those require separately recorded acceptance checks against their actual target.

## License

Devgraph's code, ontology, and bundled skills/plugins are licensed under
**AGPL-3.0-only**. See [LICENSE](LICENSE) for the terms and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party material.
Separately installed dependencies and native companions retain their own terms.
