import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions } from './monitor_test_helpers.mjs';

function arenaFilter() {
  return contextWithFunctions(['filterGraphByArena', 'filterGraph'], {});
}

function fixture() {
  const nodes = [
    { key: 'Arena:a', category: 'arena', kind: 'Arena', id: 'a' },
    { key: 'Arena:b', category: 'arena', kind: 'Arena', id: 'b' },
    { key: 'Arena:empty', category: 'arena', kind: 'Arena', id: 'empty' },
    { key: 'Initiative:a', category: 'work', kind: 'Initiative', id: 'a' },
    { key: 'Task:a', category: 'work', kind: 'Task', id: 'a' },
    { key: 'Task:b', category: 'work', kind: 'Task', id: 'b' },
    { key: 'Observation:a', category: 'observation', kind: 'InitiativeObservation', id: 'a' },
    { key: 'Observation:b', category: 'observation', kind: 'InitiativeObservation', id: 'b' },
    { key: 'Observation:unattached', category: 'observation', kind: 'InitiativeObservation', id: 'unattached' },
    { key: 'Receipt:a', category: 'receipt', kind: 'EventReceipt', id: 'a' },
    { key: 'Task:incoming-only', category: 'work', kind: 'Task', id: 'incoming-only' },
    { key: 'Task:unattached', category: 'work', kind: 'Task', id: 'unattached' },
  ];
  const edges = [
    { source: 'Arena:a', target: 'Initiative:a', relationship: 'HAS_WORK' },
    { source: 'Initiative:a', target: 'Task:a', relationship: 'HAS_CHILD' },
    { source: 'Task:a', target: 'Observation:a', relationship: 'HAS_ARTIFACT' },
    { source: 'Task:a', target: 'Receipt:a', relationship: 'EMITTED_EVENT' },
    { source: 'Arena:b', target: 'Task:b', relationship: 'HAS_WORK' },
    { source: 'Task:b', target: 'Observation:b', relationship: 'HAS_ARTIFACT' },
    { source: 'Task:incoming-only', target: 'Arena:a', relationship: 'DEPENDS_ON' },
    { source: 'Arena:a', target: 'absent', relationship: 'HAS_WORK' },
    { source: 'absent', target: 'Observation:unattached', relationship: 'HAS_ARTIFACT' },
  ];
  return { nodes, edges };
}

function nodeKeys(result) {
  return Array.from(result.nodes, node => node.key).sort();
}

function edgeKeys(result) {
  return Array.from(result.edges, edge => `${edge.source}|${edge.relationship}|${edge.target}`).sort();
}

test('empty or null Arena scope shows every node while discarding dangling relationships', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  for (const key of ['', null, undefined]) {
    const result = c.filterGraphByArena(nodes, edges, key);
    assert.deepEqual(nodeKeys(result), nodes.map(node => node.key).sort());
    assert.equal(result.edges.length, 7);
    assert.ok(result.edges.every(edge => nodes.some(node => node.key === edge.source) && nodes.some(node => node.key === edge.target)));
  }
});

test('one Arena includes its root and directed linked records, excluding unattached and incoming-only records', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const result = c.filterGraphByArena(nodes, edges, 'Arena:a');
  assert.deepEqual(nodeKeys(result), ['Arena:a', 'Initiative:a', 'Observation:a', 'Receipt:a', 'Task:a']);
  assert.deepEqual(edgeKeys(result), [
    'Arena:a|HAS_WORK|Initiative:a',
    'Initiative:a|HAS_CHILD|Task:a',
    'Task:a|EMITTED_EVENT|Receipt:a',
    'Task:a|HAS_ARTIFACT|Observation:a',
  ]);
});

test('all Arenas unions reachable records and includes empty Arena roots without unrelated observations', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const result = c.filterGraphByArena(nodes, edges, '*');
  assert.deepEqual(nodeKeys(result), [
    'Arena:a', 'Arena:b', 'Arena:empty', 'Initiative:a', 'Observation:a', 'Observation:b', 'Receipt:a', 'Task:a', 'Task:b',
  ]);
  assert.equal(result.edges.length, 6);
});

test('missing and non-Arena roots do not turn into an unscoped graph', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const invalidRoots = [
    { key: 'wrong-category', category: 'work', kind: 'Arena' },
    { key: 'wrong-kind', category: 'arena', kind: 'Project' },
  ];
  for (const key of ['not-present', 'Task:a', ...invalidRoots.map(node => node.key)]) {
    const result = c.filterGraphByArena([...nodes, ...invalidRoots], edges, key);
    assert.equal(result.nodes.length, 0, key);
    assert.equal(result.edges.length, 0, key);
  }
});

test('cycles and self-edges terminate without duplicating nodes or dropping valid stored relationships', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const cyclic = [
    ...edges,
    { source: 'Task:a', target: 'Initiative:a', relationship: 'DEPENDS_ON' },
    { source: 'Observation:a', target: 'Arena:a', relationship: 'EVIDENCE_FOR' },
    { source: 'Task:a', target: 'Task:a', relationship: 'SELF_REFERENCE' },
  ];
  const result = c.filterGraphByArena(nodes, cyclic, 'Arena:a');
  assert.deepEqual(nodeKeys(result), ['Arena:a', 'Initiative:a', 'Observation:a', 'Receipt:a', 'Task:a']);
  assert.equal(result.edges.length, 7);
  assert.ok(result.edges.some(edge => edge.relationship === 'SELF_REFERENCE'));
});

test('outgoing dependencies can cross Arena boundaries without including the target’s reverse ancestors', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  edges.push({ source: 'Task:a', target: 'Task:b', relationship: 'DEPENDS_ON' });
  const result = c.filterGraphByArena(nodes, edges, 'Arena:a');
  assert.deepEqual(nodeKeys(result), ['Arena:a', 'Initiative:a', 'Observation:a', 'Observation:b', 'Receipt:a', 'Task:a', 'Task:b']);
  assert.ok(!result.nodes.some(node => node.key === 'Arena:b'));
  assert.ok(result.edges.some(edge => edge.source === 'Task:a' && edge.target === 'Task:b'));
  assert.ok(!result.edges.some(edge => edge.source === 'Arena:b'));
});

test('an outgoing edge to another Arena traverses that Arena and its outgoing descendants', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  edges.push({ source: 'Task:a', target: 'Arena:b', relationship: 'DEPENDS_ON' });
  const result = c.filterGraphByArena(nodes, edges, 'Arena:a');
  assert.ok(result.nodes.some(node => node.key === 'Arena:b'));
  assert.ok(result.nodes.some(node => node.key === 'Task:b'));
  assert.ok(result.nodes.some(node => node.key === 'Observation:b'));
  assert.ok(!result.nodes.some(node => node.key === 'Arena:empty'));
});

test('an absent intermediate node cannot provide a path to an otherwise valid observation', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const result = c.filterGraphByArena(nodes, edges, 'Arena:a');
  assert.ok(!result.nodes.some(node => node.key === 'Observation:unattached'));
  assert.ok(!result.edges.some(edge => edge.source === 'absent' || edge.target === 'absent'));
  const withIntermediate = c.filterGraphByArena([...nodes, { key: 'absent', category: 'work', kind: 'Task' }], edges, 'Arena:a');
  assert.ok(withIntermediate.nodes.some(node => node.key === 'Observation:unattached'), 'the same edge path becomes reachable only when every intermediate record exists');
});

test('category visibility applies after reachability through hidden Work and Arena nodes', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const scoped = c.filterGraphByArena(nodes, edges, 'Arena:a');
  const observationsOnly = c.filterGraph(scoped.nodes, scoped.edges, new Set(['observation']));
  assert.deepEqual(nodeKeys(observationsOnly), ['Observation:a']);
  assert.equal(observationsOnly.edges.length, 0);
  const noArena = c.filterGraph(scoped.nodes, scoped.edges, new Set(['work', 'observation', 'receipt']));
  assert.deepEqual(nodeKeys(noArena), ['Initiative:a', 'Observation:a', 'Receipt:a', 'Task:a']);
  assert.equal(noArena.edges.length, 3);
  const noCategories = c.filterGraph(scoped.nodes, scoped.edges, new Set());
  assert.equal(noCategories.nodes.length, 0);
  assert.equal(noCategories.edges.length, 0);
});

test('a new snapshot recomputes reachability from changed edges without retaining an old scope cache', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  assert.ok(c.filterGraphByArena(nodes, edges, 'Arena:a').nodes.some(node => node.key === 'Observation:a'));
  const changed = edges.filter(edge => !(edge.source === 'Task:a' && edge.target === 'Observation:a'));
  changed.push({ source: 'Task:a', target: 'Observation:unattached', relationship: 'HAS_ARTIFACT' });
  const refreshed = c.filterGraphByArena(nodes, changed, 'Arena:a');
  assert.ok(!refreshed.nodes.some(node => node.key === 'Observation:a'));
  assert.ok(refreshed.nodes.some(node => node.key === 'Observation:unattached'));
  const removedRoot = c.filterGraphByArena(nodes.filter(node => node.key !== 'Arena:a'), changed, 'Arena:a');
  assert.equal(removedRoot.nodes.length, 0);
});

test('empty Arenas and graphs without Arena records produce explicit empty scopes', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const emptyArena = c.filterGraphByArena(nodes, edges, 'Arena:empty');
  assert.deepEqual(nodeKeys(emptyArena), ['Arena:empty']);
  assert.equal(emptyArena.edges.length, 0);
  const noArenas = c.filterGraphByArena(nodes.filter(node => node.category !== 'arena'), edges, '*');
  assert.equal(noArenas.nodes.length, 0);
  assert.equal(noArenas.edges.length, 0);
  assert.equal(c.filterGraphByArena([], [], '*').nodes.length, 0);
  assert.equal(c.filterGraphByArena([], [], null).nodes.length, 0);
});

test('Arena and category filtering do not mutate snapshot records or arrays', () => {
  const c = arenaFilter();
  const { nodes, edges } = fixture();
  const before = JSON.stringify({ nodes, edges });
  for (const record of [...nodes, ...edges]) Object.freeze(record);
  Object.freeze(nodes); Object.freeze(edges);
  for (const key of [null, '*', 'Arena:a', 'Arena:empty', 'absent']) {
    const scoped = c.filterGraphByArena(nodes, edges, key);
    c.filterGraph(scoped.nodes, scoped.edges, new Set(['observation']));
  }
  assert.equal(JSON.stringify({ nodes, edges }), before);
});
