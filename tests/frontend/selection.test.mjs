import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultProfile, evaluateScore, evaluateDecision, runSelection, fromMonitorSnapshot, evaluateExpression, solveClosure, canonicalJSON, validateProfile} from '../../src/devgraph/frontend/static/selection/core/index.mjs';
import {exampleDecision} from '../../src/devgraph/frontend/static/selection/page/example.mjs';

const copy = x => JSON.parse(JSON.stringify(x));
test('shared prerequisite selected once; exact cut and replayable fingerprint', async () => {
  const d = exampleDecision(), r = await runSelection(d);
  assert.equal(r.totals.utility, 9); assert.equal(r.totals.benefit, 72); assert.equal(r.totals.burden, 63); assert.equal(r.totals.costs[0].amount, 21);
  assert.equal(r.solution.selected.length, 3); assert.equal(r.solution.C, 33000); assert.equal(r.solution.M, 33001); assert.equal(r.solution.cut_capacity, 24000);
  assert.equal(r.rows.find(r => r.key.endsWith('platform')).required_by.length, 2);
  assert.deepEqual(r, await runSelection(copy(d)));
  const pending = runSelection(d); d.profile.resources[0].conversion = 100; assert.equal((await pending).totals.utility, 9);
});
test('approved multi-predictor confidence calculation and cost counted once across goals', () => {
  const p = defaultProfile(); p.confidence_mode = 'conservative_interpolation_v1'; p.goals[0].predictors = [{id: 'a', unit: 'ratio', reference: 1, weight: .7, direction: 1}, {id: 'b', unit: 'ratio', reference: 1, weight: .3, direction: 1}];
  const node = {key: 'Project:a', version: '1'};
  const obs = {work_version: '1', context: {baseline: p.baseline, horizon: p.horizon}, values: {impact__a: {point: .6, adverse: 0, confidence: .8, unit: 'ratio', source: 'fixture'}, impact__b: {point: .4, adverse: 0, confidence: .5, unit: 'ratio', source: 'fixture'}, effort__estimate: {point: 8, adverse: 13, confidence: .6, unit: 'points', source: 'fixture'}}};
  const score = evaluateScore(node, obs, p); assert.ok(Math.abs(score.benefit - 39.6) < 1e-10); assert.equal(score.costs[0].amount, 10); assert.ok(Math.abs(score.utility - 9.6) < 1e-10);
  obs.values.effort__estimate.confidence = .3; assert.equal(evaluateScore(node, obs, p).costs[0].amount, 11.5);
  p.goals[0].weight = .5; p.goals.push({...copy(p.goals[0]), id: 'other'}); obs.values.other__a = obs.values.impact__a; obs.values.other__b = obs.values.impact__b;
  assert.equal(evaluateScore(node, obs, p).burden, 34.5);
});
test('missing, stale, unit mismatch, and unknown confidence cannot become zero', () => {
  const d = exampleDecision(), n = d.snapshot.nodes[0], o = d.observations[n.key]; o.values.effort__estimate.point = null;
  assert.throws(() => evaluateScore(n, o, d.profile), {code: 'incomplete_score'});
  o.values.effort__estimate.point = 0; assert.equal(evaluateScore(n, o, d.profile).burden, 0);
  o.work_version = '2'; assert.throws(() => evaluateScore(n, o, d.profile), {code: 'estimate_version'}); o.work_version = '1';
  o.values.effort__estimate.unit = 'hours'; assert.throws(() => evaluateScore(n, o, d.profile), {code: 'incomplete_score'}); o.values.effort__estimate.unit = 'points';
  d.profile.confidence_mode = 'conservative_interpolation_v1'; o.values.impact__kpi.confidence = null;
  assert.throws(() => evaluateScore(n, o, d.profile), {code: 'incomplete_score'});
});
test('benefit harm and lower-is-better normalization preserve signs and adverse direction', () => {
  const d = exampleDecision(), n = d.snapshot.nodes[0], o = d.observations[n.key]; d.profile.goals[0].predictors[0].direction = -1;
  Object.assign(o.values.impact__kpi, {point: -40, adverse: -10, confidence: .5}); d.profile.confidence_mode = 'conservative_interpolation_v1';
  assert.equal(evaluateScore(n, o, d.profile).benefit, 25);
  Object.assign(o.values.impact__kpi, {point: 5, adverse: 10}); assert.ok(Math.abs(evaluateScore(n, o, d.profile).benefit + 7.5) < 1e-10);
});
test('fixed anchors do not change scores with unrelated candidates; importance is not renormalized by confidence', () => {
  const d = exampleDecision(); d.profile.confidence_mode = 'conservative_interpolation_v1';
  const score = evaluateDecision(d).scores[0]; d.snapshot.nodes.push({key: 'Project:unrelated', kind: 'Project', id: 'unrelated', title: 'Unrelated', version: '1', status: 'draft'});
  assert.deepEqual(evaluateDecision(d).scores[0], score);
});
test('scope catches omitted prerequisites, missing version, legacy coverage, blockers and parent/child double counting', () => {
  const d = exampleDecision(); d.snapshot.nodes.pop(); assert.throws(() => evaluateDecision(d), {code: 'missing_dependency'});
  const legacy = fromMonitorSnapshot({graph_nodes: [], graph_edges: [], generated_at: 'now'}); assert.equal(legacy.coverage.status, 'unknown');
  const unknown = exampleDecision(); unknown.snapshot.coverage.status = 'unknown'; assert.throws(() => evaluateDecision(unknown), {code: 'coverage_unknown'});
  const missing = exampleDecision(); missing.snapshot.nodes[0].version = null; assert.throws(() => evaluateDecision(missing), {code: 'missing_version'});
  const gap = exampleDecision(); gap.snapshot.coverage.unresolved.push({work_key: gap.seeds[0], reason: 'hidden prerequisite'}); assert.throws(() => evaluateDecision(gap), {code: 'unresolved_dependency'});
  const b = exampleDecision(); b.snapshot.blocks = b.snapshot.dependencies; b.snapshot.dependencies = []; assert.equal(evaluateDecision(b).scope.added.length, 1); b.profile.blocks_policy = 'unresolved'; assert.throws(() => evaluateDecision(b), {code: 'unresolved_blocker'});
  const h = exampleDecision(); h.snapshot.hierarchy.push({parent: h.seeds[0], child: 'Issue:intermediate'}, {parent: 'Issue:intermediate', child: h.seeds[1]}); assert.throws(() => evaluateDecision(h), {code: 'overlapping_scope'});
});
test('accepted and archived are not completed obligations; all-negative network chooses empty set', async () => {
  const d = exampleDecision(); d.snapshot.nodes[2].status = 'accepted'; assert.equal((await runSelection(d)).solution.selected.length, 3);
  d.snapshot.nodes[2].status = 'archived'; assert.equal((await runSelection(d)).solution.selected.length, 3);
  d.profile.resources[0].conversion = 20; assert.equal((await runSelection(d)).solution.selected.length, 0);
});
test('safe custom functions use confidence-adjusted inputs, direct net does not subtract cost again', async () => {
  const d = exampleDecision(); d.profile.model = {mode: 'custom_v1', benefit: 'max(0, impact__kpi) * 100', costs: {effort: 'effort__estimate'}};
  assert.equal((await runSelection(d)).totals.utility, 9);
  d.profile.model = {mode: 'custom_net_v1', expression: 'benefit - burden_effort'}; assert.equal((await runSelection(d)).totals.utility, 9);
  assert.equal(evaluateExpression('sqrt(9) + min(4, 2) * -3', {}), -3);
  for (const source of ['globalThis.fetch(1)', 'constructor(1)', '1 / 0', 'sqrt(-1)', 'x[0]', 'Math.max(1, 2)', '1; alert(1)', '('.repeat(70) + '1' + ')'.repeat(70)]) assert.throws(() => evaluateExpression(source, {}));
});
test('validation rejects ambiguous versions, weights, duplicates and unsafe capacity', () => {
  const p = defaultProfile(); p.goals[0].weight = .2; assert.throws(() => validateProfile(p), {code: 'weights'});
  assert.throws(() => solveClosure([{key: 'a', utility: Infinity}], []));
  assert.throws(() => solveClosure([{key: 'a', utility: Number.MAX_SAFE_INTEGER}], []));
  assert.throws(() => solveClosure([{key: 'a', utility: 1}, {key: 'a', utility: 2}], []));
  assert.throws(() => canonicalJSON({a: NaN}));
});
test('max-flow agrees with exhaustive closure enumeration, including cycles, negatives and ties', () => {
  let seed = 1784; const random = n => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed % n; };
  for (let trial = 0; trial < 240; trial++) {
    const nodes = Array.from({length: 7}, (_, i) => ({key: String(i), utility: random(21) - 10}));
    const edges = []; for (let a = 0; a < 7; a++) for (let b = 0; b < 7; b++) if (a !== b && random(9) === 0) edges.push({dependent: String(a), prerequisite: String(b)});
    let best = 0;
    for (let mask = 0; mask < 128; mask++) {
      if (edges.some(e => (mask & (1 << Number(e.dependent))) && !(mask & (1 << Number(e.prerequisite))))) continue;
      const value = nodes.reduce((s, n, i) => s + (mask & (1 << i) ? n.utility : 0), 0); best = Math.max(best, value);
    }
    const result = solveClosure(nodes, edges, 1); assert.equal(result.quantized_utility, best);
    const selected = new Set(result.selected); assert.ok(edges.every(e => !selected.has(e.dependent) || selected.has(e.prerequisite)));
    assert.equal(result.C - result.cut_capacity, best);
    assert.deepEqual(solveClosure([...nodes].reverse(), [...edges].reverse(), 1).selected, result.selected);
  }
});
test('read windows and numeric versions fail before importing malformed state', () => {
  const raw = {graph_nodes: [{category: 'work', key: 'Project:a', id: 'a', kind: 'Project', version: 9007199254740992}], graph_edges: []};
  assert.throws(() => fromMonitorSnapshot(raw), {code: 'node_version'});
  const d = exampleDecision(); d.snapshot.coverage.read_finished_at = {}; assert.throws(() => evaluateDecision(d), {code: 'coverage_time'});
  d.snapshot.coverage.read_finished_at = '2020-01-01T00:00:00Z'; assert.throws(() => evaluateDecision(d), {code: 'coverage_time'});
});
test('forecast context changes require review; importance changes leave evidence intact', () => {
  const d = exampleDecision(); d.profile.goals[0].scale = 80; assert.equal(evaluateDecision(d).problems.length, 0);
  d.profile.horizon = 'A different horizon'; assert.equal(evaluateDecision(d).problems.length, 3);
  assert.equal(evaluateDecision(d).problems[0].code, 'estimate_context');
});
test('nonfinite normalized bounds are rejected even when confidence is off', () => {
  const d = exampleDecision(); d.profile.goals[0].predictors[0].reference = 1e-320;
  const n = d.snapshot.nodes[0]; Object.assign(d.observations[n.key].values.impact__kpi, {point: 0, adverse: -1});
  assert.throws(() => evaluateScore(n, d.observations[n.key], d.profile), {code: 'incomplete_score'});
});
