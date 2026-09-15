import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const frontend = readFileSync(new URL('../../src/devgraph/frontend/app.py', import.meta.url), 'utf8');
const names = [
  'graphLane', 'stableGraphDepth', 'baseGraphCoordinates', 'syncGraphPhysics',
  'filterGraph', 'filterGraphByArena', 'neighborhoodKeys', 'updateGraphVisibility', 'render', 'refresh',
  'synchronizeCredential', 'applySnapshot',
];
const source = names.map(name => {
  const declaration = new RegExp(`^    (?:async )?function ${name}\\(`, 'm').exec(frontend);
  assert.ok(declaration, `${name} must exist in the shipped frontend`);
  const end = frontend.indexOf('\n    }', declaration.index);
  return frontend.slice(declaration.index, end + 6);
}).join('\n');

const categories = ['work', 'observation', 'receipt'];
const nodes = [
  { key: 'work-a', id: 'a', kind: 'Project', category: 'work' },
  { key: 'work-b', id: 'b', kind: 'Issue', category: 'work' },
  { key: 'observation', id: 'c', kind: 'InitiativeObservation', category: 'observation' },
  { key: 'receipt', id: 'd', kind: 'EventReceipt', category: 'receipt' },
];
const edges = [
  { source: 'work-a', target: 'work-b', relationship: 'HAS_CHILD' },
  { source: 'work-a', target: 'observation', relationship: 'HAS_ARTIFACT' },
  { source: 'work-b', target: 'receipt', relationship: 'EMITTED_EVENT' },
  { source: 'receipt', target: 'missing', relationship: 'DANGLING' },
];

function snapshot(graphNodes = nodes, graphEdges = edges) {
  return {
    graph_nodes: graphNodes, graph_edges: graphEdges, total_work: 2,
    active_initiatives: 0, observation_count: 1, pending_receipts: 1, receipt_count: 1,
    generated_at: '2026-09-12T12:00:00Z', storage: { ready: true, detail: 'fixture' },
    observation_by_status: { unclaimed: 1 }, work_by_kind: { Project: 1, Issue: 1 },
    recent_activity: [],
  };
}

function monitor() {
  const elements = new Map();
  const c = vm.createContext({
    AbortController,
    state: { snapshot: null, observations: [], selectedGraphKey: null, authEpoch: 0, refreshPromise: null, refreshQueued: false },
    detailState: { credential: 'synthetic-test-credential' },
    graphView: {
      visibleCategories: new Set(categories), nodes: [], edges: [],
      nodePositions: new Map(), nodeVelocities: new Map(), pointers: new Map(), pinnedKey: null, fitted: false,
    },
    tokenInput: { value: 'synthetic-test-credential' },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { className: '', textContent: '' });
      return elements.get(id);
    } },
    text() {}, relativeTime() { return 'now'; }, pauseGraphOrbit() {},
    renderPipeline() {}, renderBars() {}, renderActivity() {}, renderObservations() {},
    renderForceControls() {}, renderArenaFilter() {}, relaxGraph() {}, fitGraph() {},
    renderGraph() {}, renderGraphSelection() {}, renderGraphSearch() {}, loadSelectedDetail() {}, queueMicrotask,
  });
  c.relaxations = 0;
  c.relaxGraph = () => { c.relaxations += 1; };
  vm.runInContext(source, c);
  return c;
}

const combinations = [
  [[], [], []],
  [['work'], ['work-a', 'work-b'], ['HAS_CHILD']],
  [['observation'], ['observation'], []],
  [['receipt'], ['receipt'], []],
  [['work', 'observation'], ['work-a', 'work-b', 'observation'], ['HAS_CHILD', 'HAS_ARTIFACT']],
  [['work', 'receipt'], ['work-a', 'work-b', 'receipt'], ['HAS_CHILD', 'EMITTED_EVENT']],
  [['observation', 'receipt'], ['observation', 'receipt'], []],
  [categories, ['work-a', 'work-b', 'observation', 'receipt'], ['HAS_CHILD', 'HAS_ARTIFACT', 'EMITTED_EVENT']],
];

for (const [selected, expectedNodes, expectedEdges] of combinations) {
  test(`categories ${selected.join(', ') || '(none)'} show only their nodes and connected edges`, () => {
    const c = monitor();
    const filtered = c.filterGraph(nodes, edges, new Set(selected));
    assert.deepEqual(Array.from(filtered.nodes, node => node.key), expectedNodes);
    assert.deepEqual(Array.from(filtered.edges, edge => edge.relationship), expectedEdges);
  });
}

test('filtering does not mutate snapshot arrays or their records', () => {
  const c = monitor();
  const original = structuredClone(snapshot());
  for (const record of [...original.graph_nodes, ...original.graph_edges]) Object.freeze(record);
  Object.freeze(original.graph_nodes);
  Object.freeze(original.graph_edges);
  Object.freeze(original);
  const before = JSON.stringify(original);
  c.state.snapshot = original;
  c.graphView.visibleCategories = new Set(['work']);
  c.updateGraphVisibility();
  assert.equal(JSON.stringify(original), before);
  assert.equal(original.graph_nodes.length, 4);
  assert.equal(original.graph_edges.length, 4);
});

test('hidden selections and positions survive until the record leaves the snapshot', () => {
  const c = monitor();
  c.state.snapshot = snapshot();
  c.updateGraphVisibility();
  const initialRelaxations = c.relaxations;
  const position = { x: 317, y: -54, z: 106 };
  const velocity = { x: .5, y: -.2, z: .1 };
  c.graphView.nodePositions.set('observation', position);
  c.graphView.nodeVelocities.set('observation', velocity);
  c.state.selectedGraphKey = 'observation';
  c.graphView.pinnedKey = 'observation';
  c.graphView.visibleCategories.delete('observation');
  c.updateGraphVisibility();
  assert.equal(c.state.selectedGraphKey, 'observation', 'graph filters must not close the selected details');
  assert.equal(c.graphView.pinnedKey, null);
  assert.equal(c.graphView.nodePositions.get('observation'), position);
  c.graphView.visibleCategories.add('observation');
  c.updateGraphVisibility();
  assert.equal(c.graphView.nodePositions.get('observation'), position);
  assert.equal(c.graphView.nodeVelocities.get('observation'), velocity);
  assert.equal(c.relaxations, initialRelaxations, 'hiding and restoring does not resettle the layout');
  c.state.snapshot = snapshot(nodes.filter(node => node.key !== 'observation'));
  c.updateGraphVisibility();
  assert.equal(c.graphView.nodePositions.has('observation'), false);
});

test('all-hidden refresh keeps choices and restoring a category reveals fresh records', async () => {
  const c = monitor();
  let next = snapshot();
  c.getJson = async path => path === '/monitor/snapshot' ? next : { items: [] };
  await c.refresh();
  c.graphView.visibleCategories.clear();
  c.updateGraphVisibility();
  const added = { key: 'new-work', id: 'new', kind: 'Task', category: 'work' };
  next = snapshot([...nodes, added], [...edges, {
    source: 'work-b', target: 'new-work', relationship: 'HAS_CHILD',
  }]);
  await c.refresh();
  assert.equal(c.state.snapshot, next, 'refresh retained the complete API snapshot');
  assert.equal(c.graphView.visibleCategories.size, 0);
  assert.equal(c.graphView.nodes.length, 0);
  assert.equal(c.graphView.edges.length, 0);
  c.graphView.visibleCategories.add('work');
  c.updateGraphVisibility();
  assert.deepEqual(Array.from(c.graphView.nodes, node => node.key), ['work-a', 'work-b', 'new-work']);
  assert.deepEqual(Array.from(c.graphView.edges, edge => [edge.source, edge.target]), [
    ['work-a', 'work-b'], ['work-b', 'new-work'],
  ]);
});
