# Project selection

Open **Project selection** from Monitor navigation, or visit `/monitor/selection`.
This is a separate client page. It recommends a set of work using the goal-conditioned
utility model; it does not create, select, update, or persist canonical Work.

## Workflow

1. Projects load automatically when a read connection is available. **Refresh** reads Work objects and their dependencies using the scoped Monitor
   credential. Nodes, source/sink
   terminals, and predictor slots appear before any estimates are supplied. Unknown
   values are `null`; dashed terminal arcs retain both possible utility signs until the
   score is known. **Load example** remains an explicitly labeled synthetic scenario.
2. The **Projects** dropdown defaults to **All projects**. Select a specific project to
   scope the network to it and its prerequisites; switch back to see all projects.
   Estimates survive switching. In All projects mode, Refresh also includes newly created
   projects. Directly checking candidate nodes creates a custom scope.
3. Edit the KPI and remaining effort directly on each node. Utility and known capacities
   update on every input without submitting a form. **Inspect** opens an inline panel for
   all predictor values, adverse estimates, confidence, sources, and the raw node schema.
   Closing the panel keeps edits already applied. A missing value never means zero.
4. Use **Model & scope** for candidate kind, baseline, horizon, goal weights, fixed
   references, effort conversion, confidence policy, or custom profiles. Candidate checkboxes
   seed the closure; prerequisites are added once. A parent and descendant cannot both be
   charged in one scope. Search dims nonmatching nodes and filters the optional table;
   it does not remove network nodes or change the decision.
5. **Run selection** freezes the inputs and solves in a cancellable Worker. Nodes show
   selected source-side work; edges show actual flow / capacity, with the minimum cut
   highlighted. Editing inputs clears those overlays and marks the previous result stale.
   Drag handles move nodes, canvas dragging pans, and zoom/Fit controls navigate. Focus a
   handle and use arrow keys to move a node; these layout changes do not change the run.
6. **Export** saves current inputs or a current run including inputs. Importing a run
   restores its inputs and requires a new solve, including older runs without per-edge
   flow. **Save draft** stores inputs in this tab. Restored live drafts reconcile with a
   fresh read, preserving estimates and flagging changed Work versions. Credentials are
   excluded from exports.

Work-version, baseline, or horizon changes require estimate review. Importance or
conversion changes preserve the observations and invalidate the previous run.
Missing prerequisites, hidden obligation markers, old coverage metadata and unavailable
Work versions block a qualified run. Accepted/archived lifecycle state does not prove
that work has been delivered; enter reviewed remaining cost instead of assuming zero.

## Models and confidence

The basic linear preset gives one goal a reference improvement worth 100 utility units
and uses effort points as cost. The user supplies all live predictions. There is no
first-class Fibonacci or project KPI field in the current Work request schema; this
page's observation adapters are explicit local records.

The **Multiple predictors & custom functions** editor accepts the versioned JSON
profile described in the [SDK contract](../sdk/project-selection.md). Add goals,
weighted KPI predictors, or calibrated resource channels there. Equal weights are
available as an explicit preset. Weights within a group, and importance across goals,
sum to one. Fixed references prevent a new candidate from rescaling existing utility.

Confidence is off by default. When enabled, the linear policy computes
`q × point + (1 − q) × adverse` on each normalized predictor. Benefit adverse values
must be no better than the point estimate; cost adverse values must be no smaller.
Unknown confidence or bounds must be supplied. Lower confidence never makes cost cheaper
under this linear policy. Confidence is evidence reliance, not importance or success
probability. Producer confidence is not imported or treated as calibrated automatically.

Custom benefit/cost or direct-net functions use a bounded arithmetic expression grammar,
not JavaScript execution. They consume normalized, confidence-adjusted inputs. For
nonmonotone custom expressions, adjusted inputs define a scenario and do not guarantee
conservative output. Costs are not deducted twice in direct-net mode.

## Scope and numerical limits

This is additive maximum-weight closure. `DEPENDS_ON` points from dependent to
prerequisite. The default declared `blocks_policy=require` reverses `BLOCKS` into a
prerequisite obligation. Set `unresolved` to block decisions involving unclassified
blockers. Shared prerequisites have one node and one cost. Parent/child hierarchy is
used to detect overlapping attribution, not automatically turned into dependencies.

Monitor now includes additive decimal Work versions and `selection_scope` metadata:
a read window, `assembled` consistency, stored-edge scan status, and visible affected
Work markers for unresolved obligations. Hidden endpoint IDs are not disclosed. This
is not an atomic database snapshot and does not verify external gates, time feasibility,
or incremental KPI attribution. The result is for the supplied scenario.

Final node utility is rounded once (default scale 1000, ties toward positive infinity).
Dependency capacity is `M=C+1` in integer units and never confidence-weighted. The result
contains a SHA-256 input fingerprint, full node/dependency manifest and min-cut certificate.
The minimal source side resolves objective ties, omitting optional zero-utility work.
Raw totals are also reported; the exact optimum guarantee applies to quantized utility.

Bounds: 3,000 Work nodes; 30,000 combined snapshot relationships; 16 goals and 16 resources;
32 predictors per group; 2,048-character / 512-token arithmetic expressions. Safe-capacity
overflow is rejected. Large runs are cancellable. A hard budget, alternative prerequisites,
mutual exclusions, schedule, shared KPI saturation, and portfolio risk are not constraints
in this solver. Raising effort conversion explores tradeoffs, not a hard-budget guarantee.

## Compatibility

Older Monitor snapshots remain readable. Missing version or dependency-coverage
metadata prevents a qualified selection run instead of inventing coverage. The
new fields are additive and do not change stored Work or existing graph fields.
