import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { contextWithFunctions, frontend, treeDocument } from './monitor_test_helpers.mjs';

const arena = { key: 'Arena:devgraph', category: 'arena', kind: 'Arena', id: 'devgraph', title: 'Devgraph' };
const task = { key: 'Task:records', category: 'work', kind: 'Task', id: 'records', title: 'Rename records' };
const outside = { ...task, key: 'Task:outside', id: 'outside', title: 'Outside records' };
const edges = [{ source: arena.key, target: task.key, relationship: 'CONTAINS_WORK' }];

function controls() {
  const document = treeDocument();
  const c = contextWithFunctions([
    'renderArenaFilter', 'reconcileChildren', 'filterGraphByArena', 'renderGraphSearch', 'setGraphArena',
  ], {
    document,
    graphView: { arenaKey: arena.key, neighborhood: 'Task:previous', visibleCategories: new Set(['arena', 'work']) },
    state: { snapshot: { graph_nodes: [arena, task, outside], graph_edges: edges } },
    pauseGraphOrbit() {}, updateGraphVisibility() {}, fitGraph() {},
  });
  vm.runInContext(/^    const make = .+$/m.exec(frontend)[0].replace('const make', 'var make'), c);
  return c;
}

test('Arena options preserve selection, element identity and focus on refresh', () => {
  const c = controls();
  c.renderArenaFilter(c.state.snapshot.graph_nodes);
  const select = c.document.getElementById('graph-arena');
  const option = select.children.find(item => item.getAttribute('value') === arena.key);
  select.focus();
  c.renderArenaFilter([{ ...arena, title: 'Devgraph revised' }, task]);
  assert.equal(select.value, arena.key);
  assert.equal(select.children.find(item => item.getAttribute('value') === arena.key), option);
  assert.equal(option.textContent, 'Devgraph revised');
  assert.equal(c.document.activeElement, select);
  assert.match(c.document.getElementById('graph-arena-note').textContent, /Dependencies can cross Arena boundaries/);
});

test('a disappeared Arena keeps an explicit missing option instead of broadening the filter', () => {
  const c = controls();
  c.renderArenaFilter([]);
  const select = c.document.getElementById('graph-arena');
  assert.equal(select.value, arena.key);
  assert.match(select.children.at(-1).textContent, /not in snapshot/);
  assert.match(c.document.getElementById('graph-arena-note').textContent, /not in this snapshot/);
  assert.equal(c.graphView.arenaKey, arena.key);
});

test('Arena titles are inert text and archived Arenas remain identifiable', () => {
  const c = controls();
  c.renderArenaFilter([{ ...arena, title: '<img src=x onerror=alert(1)>', archived: true }]);
  const option = c.document.getElementById('graph-arena').children.at(-1);
  assert.equal(option.textContent, '<img src=x onerror=alert(1)> (archived)');
  assert.equal(option.children.length, 0);
});

test('graph search is constrained by Arena reachability but still finds hidden categories', () => {
  const c = controls();
  c.graphView.visibleCategories.clear();
  c.document.getElementById('graph-search').value = 'records';
  c.renderGraphSearch();
  const results = c.document.getElementById('graph-search-results');
  assert.match(results.textContent, /1 matches reachable/);
  assert.match(results.textContent, /Rename records/);
  assert.match(results.textContent, /hidden category/);
  assert.doesNotMatch(results.textContent, /Outside records/);
});

test('changing Arena clears neighborhood, updates visibility and fits the scoped view', () => {
  const c = controls(), calls = [];
  c.pauseGraphOrbit = () => calls.push('pause');
  c.updateGraphVisibility = () => calls.push('update');
  c.fitGraph = () => calls.push('fit');
  c.setGraphArena('*');
  assert.equal(c.graphView.arenaKey, '*');
  assert.equal(c.graphView.neighborhood, null);
  assert.deepEqual(calls, ['pause', 'update', 'fit']);
});
