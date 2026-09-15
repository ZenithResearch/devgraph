# Shared selection module contract, v1

The beta serves dependency-free ESM modules from
`/monitor/selection-assets/core/index.mjs`. The same files are packaged under
`src/devgraph/frontend/static/selection/core/`, with TypeScript declarations.
They require no DOM, Wallet connection, or Rust/WASM initialization. The separate
`@devgraph/web` browser SDK remains outside this beta.

```js
import {
  fromMonitorSnapshot, defaultProfile, evaluateDecision,
  validateDecision, runSelection,
} from '/monitor/selection-assets/core/index.mjs';

const snapshot = fromMonitorSnapshot(authorizedMonitorResponse);
const profile = defaultProfile();
const decision = validateDecision({
  schema: 'devgraph.selection-decision.v1',
  profile, snapshot,
  seeds: ['Project:example'],
  observations: {
    'Project:example': {
      work_version: '1',
      context: {baseline: profile.baseline, horizon: profile.horizon},
      values: {
        impact__kpi: {
          point: 42, adverse: 20, confidence: 0.8,
          unit: 'KPI units', source: 'Reviewed forecast',
        },
        effort__estimate: {
          point: 8, adverse: 13, confidence: 0.8,
          unit: 'points', source: 'Team remaining-work estimate',
        },
      },
    },
  },
});
const readiness = evaluateDecision(decision); // scope, scores, per-work problems
const run = await runSelection(decision);    // rejects incomplete prerequisite inputs
console.log(run.totals.utility, run.solution.selected, run.fingerprint);
```

The snippet assumes a matching Work identity/version in the authorized snapshot. Every
node in its prerequisite closure needs reviewed observations. Read authorization is
owned by the host application; the pure selection module does not mint or fetch data.

## Initialize the network before entering inputs

```js
import {initializeDecision, describeNetwork, resolveScope} from '/monitor/selection-assets/core/index.mjs';

const decision = initializeDecision(snapshot, profile, ['Project:example']);
// Every Work node now has typed, null-valued predictor observations.
const scope = resolveScope(decision.snapshot, decision.seeds, decision.profile);
let network = describeNetwork({...scope, profile, observations: decision.observations});
// Render network.terminals, network.nodes[].variables and network.edges now.
// Missing input -> score null, capacity null, status 'needs_inputs'.

decision.observations['Project:example'].values.effort__estimate.point = 8;
decision.observations['Project:example'].values.effort__estimate.source = 'Team estimate';
network = describeNetwork({...scope, profile, observations: decision.observations});
// Re-render known values. Other missing predictors and dependencies remain visible.
```

`initializeDecision` creates a detached decision with the Work versions and forecast
context bound to typed predictor slots. It never invents observations or writes Work.
`describeNetwork` returns a JSON-compatible `devgraph.selection-network.v1` view. Each
node includes its Work object, predictor definitions, values, required field names, and
its score or validation problem. Source and sink have reserved keys `$source` / `$sink`.
A node of unknown sign has two symbolic terminal arcs (`max(u(i),0)` and `max(-u(i),0)`);
these are unresolved possibilities, not simultaneous scored contributions. Known nodes
get their signed, quantized capacities. Dependency capacity remains unknown until all
scores are available and `C` and `M` can be compiled. Zero capacity and null differ.

This view validates numeric topology, but does not certify snapshot coverage or candidate
closure; call `resolveScope` / `evaluateDecision` before treating it as a decision network.
Monitor can still display known nodes and edges when coverage blocks a qualified solve.

`compileNetwork(scores, dependencies, scale)` is the shared exact numeric compiler used
by the visual client and solver. New solutions add `network_edges`, containing each arc's
integer capacity, actual flow, and cut membership. Existing `cut`, weights, and run fields
remain unchanged; the TypeScript field is optional to admit previously exported runs.
Flow and cut overlays describe the last frozen run, not pending input edits.

## Records and functions

- `DecisionProfile`: `devgraph.selection-profile.v1`; ID/version, baseline, horizon,
  utility unit, goals, resources, model, confidence policy, blocker policy and quantization.
- `SelectionSnapshot`: `devgraph.selection-snapshot.v1`; canonical Work identities with
  decimal-string versions, dependencies, blockers, hierarchy and coverage. Missing version
  is null; unsafe numeric versions are rejected by the Monitor adapter.
- `WorkObservations`: work version, baseline/horizon context and predictor-keyed values.
  A key is `group_id__predictor_id`. Each value contains point, adverse, confidence, unit
  and source. Optional observation timestamps, evidence refs and other JSON provenance
  remain in the frozen input and fingerprint. Null is not zero.
- `Score`: individual normalized/effective contributions, source, weights, goal benefits,
  resource amounts, converted burden, net utility and confidence/model treatment.
- `SelectionRun`: `devgraph.selection-run.v1`; frozen inputs, fingerprint, selection,
  dependency explanations, totals, integer weights, cut edges and numerical/tie policy.

`validateProfile`, `validateSnapshot`, and `validateDecision` validate structural inputs.
`resolveScope` validates coverage and adds the prerequisite closure. `evaluateScore`
validates predictor completeness and context. `evaluateDecision` reports per-work input
problems without pretending they are zero. `runSelection` clones inputs before its first
async boundary and refuses incomplete evaluations. `solveClosure` is also available as
a pure numeric solver. `SelectionError` has a stable `code`, message and details.

Unknown top-level schema versions and model/policy enums fail closed. Additional JSON
provenance is preserved but has no scoring effect. Existing Work request bodies, signed
operations, Rust protocol pins and receipt formats are unchanged. Client selection
records are not new canonical Work types or persisted Work fields.

## Model equations

For goal g and resource r:

```
z_gj = direction × forecast_delta / reference_improvement
b_g  = goal_scale × sum_j(predictor_weight × effective_z_gj)
c_r  = cost_scale × sum_k(predictor_weight × effective_quantity/reference_quantity)
u(i) = sum_g(goal_importance × b_g) − sum_r(conversion × c_r)
u(A) = sum_i_in_A u(i)
```

All group weights are nonnegative and sum to one. Normalize with fixed reference anchors.
Costs are deducted once outside the goal sum. Resource scale/unit and utility conversion
must be explicit. Baseline/horizon changes require renewed forecast review; reweighting
never overwrites observations. Predictor direction ±1 handles improvement or harm.

With `confidence_mode=none`, effective values are point values and confidence remains
metadata. `conservative_interpolation_v1` requires confidence in [0,1] and a valid adverse
scenario. It interpolates normalized inputs with unchanged weights. It is a declared
planning policy, not a calibrated posterior. Dependencies always remain hard constraints.

A custom profile can use:

```json
{"mode":"custom_v1","benefit":"100 * max(0, impact__kpi)","costs":{"effort":"effort__estimate"}}
```

Or define net utility directly:

```json
{"mode":"custom_net_v1","expression":"benefit - burden_effort"}
```

Symbols include declared normalized/effective predictor keys, the base linear `benefit`,
`cost_<resource_id>` and `burden_<resource_id>`. Each custom expression sees the same frozen
base inputs. Omitted custom cost expressions retain the linear resource amount. Direct-net
mode does not subtract costs again; reported base benefit/cost are context, not a purported
algebraic decomposition of the custom net value. The per-goal breakdown stays the base
linear context for custom models. Nonlinear within-node functions remain compatible with
closure; cross-selected-set interactions do not. Nonmonotone custom expressions do not
inherit the linear confidence policy's conservative-output guarantee.

Grammar: numeric literals, declared symbols, parentheses, unary signs, +, −, *, /,
`min`, `max`, `abs`, `sqrt`. Property access, arbitrary calls, hidden network/state,
randomness, time and nonfinite results are unavailable. Confidence is applied once before
expression evaluation. Function content is included in the input digest.

## Build and compatibility verification

Canonical source: `src/devgraph/frontend/static/selection/core/`. Python packages those
original files as resources. `packages/web/scripts/build-selection.mjs` cleans and copies
that tree into `dist/selection`, emitting `manifest.json` SHA-256 digests. The full SDK
build invokes it and records the digests. There is no independently maintained solver copy.

```
node packages/web/scripts/build-selection.mjs
node --test packages/web/test/selection*.test.mjs
cd packages/web
npm run typecheck:selection
```

The standalone command builds selection modules only. Run the existing full `npm run build`
when assembling the complete SDK with the authentication/WASM runtime. Do not interpret a
selection-only build as qualification or publication of the Wallet/provider stack.

Tests compare the complete packaged file set and every byte with the Monitor source,
import the public subpath in Node, typecheck a consumer, and compare 240 small-graph
optima with exhaustive enumeration. Browser tests run the real shipped page and Worker
against the disposable in-memory preview. See the [operator guide](../user/project-selection.md)
for limits, numerical semantics and provenance constraints.
