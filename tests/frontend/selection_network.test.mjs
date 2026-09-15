import test from 'node:test';
import assert from 'node:assert/strict';
import {compileNetwork, describeNetwork, initializeDecision, runSelection, evaluateDecision} from '../../src/devgraph/frontend/static/selection/core/index.mjs';
import {exampleDecision} from '../../src/devgraph/frontend/static/selection/page/example.mjs';
import {layoutFlow, edgeGeometry} from '../../src/devgraph/frontend/static/selection/page/flow-layout.mjs';

test('network schema precedes observations, includes typed variables and unresolved terminal arcs', () => {
  const sample = exampleDecision(), d = initializeDecision(sample.snapshot, sample.profile, sample.seeds);
  assert.equal(evaluateDecision(d).problems.length, 3);
  const network = describeNetwork({...d.snapshot, profile: d.profile, observations: d.observations});
  assert.equal(network.schema, 'devgraph.selection-network.v1'); assert.equal(network.status, 'needs_inputs');
  assert.equal(network.nodes.length, 3); assert.equal(network.edges.length, 8); assert.equal(network.C, null); assert.equal(network.M, null);
  for (const n of network.nodes) {
    assert.equal(n.variables.length, 2); assert.equal(n.score, null);
    for (const v of n.variables) { assert.equal(v.point, null); assert.equal(v.confidence, null); assert.ok(v.unit); assert.deepEqual(v.required_fields, ['point', 'source']); }
  }
  assert.ok(network.edges.every(e => e.capacity === null)); assert.doesNotThrow(() => JSON.stringify(network));
  // Initialization is detached and never overwrites canonical Work or the source profile.
  d.profile.horizon = 'later'; assert.notEqual(d.profile.horizon, sample.profile.horizon);
});
test('partial edits resolve only known capacities; zero is distinct from unknown', () => {
  const d = exampleDecision(), key = d.snapshot.nodes[0].key;
  d.observations[key].values.effort__estimate.point = null;
  let n = describeNetwork({...d.snapshot, profile: d.profile, observations: d.observations});
  assert.equal(n.C, null); assert.ok(n.edges.some(e => e.from === '$source' && e.to === key && e.capacity === null));
  assert.ok(n.edges.some(e => e.from === 'Project:platform' && e.to === '$sink' && e.capacity === 24000));
  d.observations[key].values.effort__estimate.point = 0;
  n = describeNetwork({...d.snapshot, profile: d.profile, observations: d.observations});
  assert.equal(n.status, 'ready'); assert.equal(n.C, 57000); assert.equal(n.M, 57001);
  assert.ok(n.edges.every(e => e.capacity !== null));
  d.profile.horizon = 'different'; assert.equal(describeNetwork({...d.snapshot, profile: d.profile, observations: d.observations}).status, 'needs_inputs');
});
test('compiled network and solved edge certificate agree; flow conserves at every Work node', async () => {
  const d = exampleDecision(), r = await runSelection(d), s = r.solution;
  const network = compileNetwork(r.rows, r.scope.dependencies, s.scale);
  assert.deepEqual(s.network_edges.map(({flow, cut, ...edge}) => edge), network.edges);
  assert.deepEqual(describeNetwork({...d.snapshot, profile: d.profile, observations: d.observations}).edges, network.edges);
  const balance = new Map();
  for (const e of s.network_edges) {
    assert.ok(Number.isSafeInteger(e.flow) && e.flow >= 0 && e.flow <= e.capacity);
    balance.set(e.from, (balance.get(e.from) || 0) - e.flow); balance.set(e.to, (balance.get(e.to) || 0) + e.flow);
  }
  for (const n of d.snapshot.nodes) assert.equal(balance.get(n.key) || 0, 0);
  assert.equal(balance.get('$sink'), s.cut_capacity); assert.equal(balance.get('$source'), -s.cut_capacity);
  assert.equal(s.network_edges.filter(e => e.cut).reduce((sum, e) => sum + e.capacity, 0), s.cut_capacity);
  assert.ok(s.network_edges.filter(e => e.cut).every(e => e.flow === e.capacity));
  assert.throws(() => compileNetwork([{key: '$source', utility: 1}], []), {code: 'node_key'});
});
test('flow layout handles cycles and long chains without overlapping cards', () => {
  const nodes = Array.from({length: 30}, (_, i) => ({key: String(i)}));
  const dependencies = nodes.slice(1).map((n, i) => ({dependent: String(i), prerequisite: n.key}));
  dependencies.push({dependent: '10', prerequisite: '3'});
  const layout = layoutFlow(nodes, dependencies); assert.equal(layout.positions.size, 32);
  const boxes = [...layout.positions.values()];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j]; assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y);
  }
  for (const e of dependencies) assert.ok(!edgeGeometry(layout.positions.get(e.dependent), layout.positions.get(e.prerequisite)).path.includes('NaN'));
});

test('new goal predictors and confidence policy initialize all required slots', () => {
  const sample = exampleDecision(), p = sample.profile; p.confidence_mode = 'conservative_interpolation_v1';
  p.goals[0].predictors[0].weight = .5;
  p.goals[0].predictors.push({id: 'retention', label: 'Retention delta', unit: 'users', weight: .5, reference: 10, direction: 1});
  const d = initializeDecision(sample.snapshot, p, sample.seeds), network = describeNetwork({...d.snapshot, profile: p, observations: d.observations});
  for (const n of network.nodes) {
    assert.equal(n.variables.length, 3); assert.equal(n.variables[1].key, 'impact__retention');
    assert.deepEqual(n.variables[1].required_fields, ['point', 'source', 'adverse', 'confidence']);
    assert.equal(n.variables[1].observed_unit, 'users'); assert.equal(n.observation_work_version, n.work.version);
    assert.equal(n.observation_context.horizon, p.horizon);
  }
});
