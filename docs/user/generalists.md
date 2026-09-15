# Devgraph for general users

Devgraph helps you see what work exists, how it connects, and the reasoning or
evidence behind it. The monitor is a reader: use it to explore and understand.
An authorized CLI or agent makes changes to the stored graph.

## Start with the demo or your own graph

For a first look, follow [Try the monitor](../../README.md#try-the-monitor).
It runs on your computer at port 4174, uses the example credential
`fake-credential-monitor`, and discards its synthetic data when stopped.
The demo does not import your projects or connect to an existing graph.

For your own work, follow [beta setup](beta.md). Someone must configure the
local host and its separate Neo4j/Java prerequisites. Open
[your local monitor](http://127.0.0.1:8080/) once that host is running. The
credential belongs to that installation; the demo credential will not work there.

To connect a configured local reader, run these commands in your own terminal:

```sh
devgraph local status
devgraph local read-credential show
```

Paste the displayed value into the monitor's credential field and select
**Connect**. Do not paste it into a chat or a bug report. It grants read access,
including readable descriptions and eligible attachments, but cannot change work.

The monitor uses the current tab's `sessionStorage` for this value. Treat it as
a tab-session credential, not a permanent login or an encrypted vault. The CLI
and agent integrations can read the configured credential themselves.

## Find work and read its plan

1. Use **Find work or a node** to search by title or ID, or select a node in the
   graph. The details panel opens the record behind it.
2. Expand **Description / plan** for its authored description, priority, and
   version. A plan may live here; Devgraph does not generate missing text.
3. Open **Related work** to load its parent, child work, dependencies, or work
   that needs it. Tasks also show **Blocked by** and **Blocks**. Use **Load more**
   when a relationship list has another page.
4. Select **Load supporting material** to resolve attached documents, external
   links, requirements, and acceptance criteria. Declared reference IDs alone
   are not the document contents. **Load more material** continues a longer list.
5. Use **Read document** for an eligible local text/Markdown attachment, or
   **Open source** for an external web link. Opening an external source leaves
   the local application; the monitor does not send its read credential there.

**Reading view** gives text more room. On a wide screen, drag the divider to
resize the details panel. On a narrow screen the layout stacks vertically.
The reader may retain earlier text while refreshing; its status tells you when
that text has not yet been revalidated.

Local document previews are optional. An operator must configure narrow document
directories, and the Artifact must be attached to the selected Work record.
Only UTF-8 text and Markdown up to 256 KiB are supported. PDF, image, and remote
document contents are not automatically imported. If the reader says roots are
unconfigured, the metadata can still be useful; it has not read the file.
See [supporting material](../supporting-material.md).

## Make the graph easier to read

Use **+ / −**, the zoom slider, or scroll/pinch to zoom. **Fit** frames the visible
graph; **Expand view** provides more space. Drag the bottom resize handle to
change graph height. You can also focus that handle and use arrow keys.
Shift-drag pans; dragging the background orbits the scene, and dragging a node
changes its displayed position. These controls do not edit stored relationships.

The **Arena**, **Work**, **Observation**, and **Receipt** checkboxes show or hide
categories. **Labels → Selected neighborhood** keeps nearby labels readable;
**All labels** can become crowded. Open **Layout settings** to adjust spacing,
settle the layout, or reset it.

**From Arena** follows the selected Arena's outgoing connections in the loaded
graph. **Any Arena** combines paths from all loaded Arenas; **All nodes** clears
that scope and also shows disconnected work. A dependency can bring another
Arena's work into view. Visibility is not membership, and filtering never moves
a Task. Membership belongs to an eligible Work root, with its descendants
inheriting it. Ask an authorized agent for the actual membership when that matters.

## Understand observations and records

| Item | What it tells you |
| --- | --- |
| Proposal | A proposed piece of work; acceptance has its own decision trail. |
| Initiative, Project, Issue, Task | Authored work at different levels, with descriptions and relationships. The usual hierarchy is Initiative → Project → Issue → Task. |
| Arena | An ongoing area of responsibility containing eligible Work roots. |
| Observation | A scout's interpretation of a public repository or organization, with a problem, desired outcome, and evidence links. |
| Receipt | The current UI name for an unsigned local operational record of a committed mutation. |

Observations begin **inferred** and **unclaimed**. They do not prove that a
maintainer agrees, owns a canonical Initiative here, or promised to implement
the suggested outcome. Select one to read **Problem**, **Desired outcome**, and
**Evidence and provenance**. Its percentage is producer confidence, not a
calibrated success probability. The monitor has no maintainer-claim action.

The `EventReceipt` API name is retained for compatibility. A **pending** receipt
means the graph change and its local record committed, while the local outbox
has not advanced. It does not mean the mutation failed. It is not a signed,
portable cryptographic receipt or proof of delivery to another system. The
Wallet/secS signatures that authorize a change are a separate mechanism.

Work statuses are **draft**, **review**, **accepted**, and **archived**. Project
and Issue progress summarize that recorded lifecycle, including accepted and
archived work as terminal. A high percentage is not independent evidence that
something was shipped or achieved its desired outcome.

## Compare possible projects

Open **Project selection** in the navigation. **Load example** demonstrates the
tool with labelled synthetic values. For your graph, select a project, enter
outcome and remaining-effort estimates, then **Run selection**. Required work is
included in the scenario; unknown estimates need review rather than becoming zero.

The result is a recommendation for the inputs you supplied. It does not assign
people, change statuses, move Work between Arenas, or create a delivery schedule.
**Save draft** keeps inputs in this tab; **Export** saves a local file for later.
Credentials are excluded from that export. Read the [project-selection guide](project-selection.md)
before treating a scenario as a decision; hard budgets and scheduling are not
constraints in this solver.

## Work with an agent

Install the [Codex or Hermes integration](agent-integrations.md), then start with
a read request: “Find the launch Issue and show its description, dependencies,
and attached plan.” Be specific about any intended change: “Update this Task's
description to the following text.” A plugin installation or read credential
does not grant write access; signed operations need separate identity and grant setup.
The native signing companions are currently private and separately distributed;
installing this public beta alone does not make signed writes available.

If a submitted change times out, ask the agent to retry the **same request with
the same idempotency key and identity**. A timeout may happen after a commit;
creating another request could duplicate work.

## What persists, and what to check when something is missing

The persistent graph lives under the data directory chosen during setup. Browser
filters and layout changes do not rewrite it. Project-selection drafts are tab
state, and the demo is process memory. Public source code is not a hosted graph,
automatic cross-device sync, or a verified backup of your work.

| What you see | Next step |
| --- | --- |
| Disconnected or unauthorized | Check `devgraph local status` and use that host's valid read credential. |
| Node appears missing | Choose **All nodes**, enable its category, and exit neighborhood mode. Then check whether the record actually exists. |
| No description | No description was stored; inspect supporting material or ask its author. |
| A missing/unavailable attachment | Read its explicit status. An unresolved reference is not an empty document. |
| “Showing saved snapshot” or earlier text | The last refresh failed or is pending; do not treat it as confirmed current data. |
| No observations | A producer must supply observations; installing Devgraph does not scout every repository automatically. |

For persistent setup and credential storage, continue with [beta setup](beta.md).
