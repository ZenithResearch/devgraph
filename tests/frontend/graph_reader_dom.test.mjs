import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { contextWithFunctions, frontend, treeDocument } from './monitor_test_helpers.mjs';

function readerTree() {
  const document = treeDocument();
  const node = { key: 'Task:read-plan', id: 'read-plan', kind: 'Task', category: 'work', title: 'Read plan', status: 'draft' };
  const c = contextWithFunctions([
    'safeSourceUrl', 'readPath', 'readerButton', 'readerSection', 'appendSource',
    'reconcileChildren', 'renderGraphSelection', 'documentStateMessage', 'relationshipLabel',
  ], {
    URL, document,
    state: { snapshot: { graph_nodes: [node], graph_edges: [] }, selectedGraphKey: node.key },
    graphView: { nodes: [node], edges: [] },
    detailState: {
      node, data: { ...node, description: 'First paragraph.\n\nSecond paragraph.', priority: 1, version: 4 },
      relations: new Map(), documents: new Map(), support: null, error: null, loading: false,
    },
    text(id, value) { document.getElementById(id).textContent = value; },
  });
  const make = /^    const make = .+$/m.exec(frontend)[0];
  vm.runInContext(make.replace('const make', 'var make'), c);
  return c;
}

test('reader renders authored multiline content as inert text and preserves its elements during refresh', () => {
  const c = readerTree();
  const authored = 'Release plan\n\n<script>window.exfiltrate()</script>\n<img src=x onerror=alert(1)>\nFinal paragraph.';
  c.detailState.data.description = authored;
  c.renderGraphSelection();
  const root = c.document.getElementById('reader-content');
  const description = root.children.find(node => node.dataset.uiKey === 'description');
  const copy = description.querySelector('.reader-description');
  assert.equal(copy.textContent, authored);
  assert.equal(copy.children.length, 0, 'authored tags remain a text node');
  root.scrollTop = 386;
  copy.focus();
  for (let i = 0; i < 3; i += 1) c.renderGraphSelection();
  assert.equal(root.children.find(node => node.dataset.uiKey === 'description'), description);
  assert.equal(description.querySelector('.reader-description'), copy);
  assert.equal(c.document.activeElement, copy);
  assert.equal(root.scrollTop, 386);
});

test('open related-work sections and their links retain identity when another reader section updates', () => {
  const c = readerTree();
  c.detailState.relations.set('children', { loaded: true, items: [{ id: 'child', kind: 'Task', title: 'Authored child task', status: 'draft' }] });
  c.renderGraphSelection();
  const root = c.document.getElementById('reader-content');
  const relationships = root.children.find(node => node.dataset.uiKey === 'relationships');
  const children = relationships.children.find(node => node.dataset.uiKey === 'children');
  children.setAttribute('open', '');
  const link = children.querySelector('button');
  link.focus();
  c.detailState.support = { items: [] };
  c.renderGraphSelection();
  assert.equal(relationships.children.find(node => node.dataset.uiKey === 'children'), children);
  assert.equal(children.hasAttribute('open'), true);
  assert.equal(children.querySelector('button'), link);
  assert.equal(c.document.activeElement, link);
});

test('a filtered reader distinguishes visible connections from all snapshot connections', () => {
  const c = readerTree();
  c.state.snapshot.graph_nodes.push({ key: 'receipt:r', kind: 'EventReceipt', category: 'receipt', id: 'r', title: 'Receipt' });
  c.state.snapshot.graph_edges = [{ source: c.detailState.node.key, target: 'receipt:r', relationship: 'EMITTED_EVENT' }];
  c.graphView.edges = [];
  c.renderGraphSelection();
  const root = c.document.getElementById('reader-content');
  const connections = root.children.find(node => node.dataset.uiKey === 'connections');
  assert.match(connections.textContent, /0 visible · 1 in this snapshot/);
  assert.match(connections.textContent, /Receipt/);
  assert.doesNotMatch(connections.textContent, /No connections/);
});

test('an inspected node outside the Arena filter offers an explicit return to all nodes', () => {
  const c = readerTree();
  c.graphView.arenaKey = 'Arena:other';
  c.graphView.arenaNodes = [];
  c.graphView.nodes = [];
  c.renderGraphSelection();
  const root = c.document.getElementById('reader-content');
  assert.match(root.textContent, /Show in all nodes/);
  assert.match(root.textContent, /outside the selected Arena reachability filter/);
  assert.doesNotMatch(root.textContent, /Show neighborhood/);
  assert.match(root.textContent, /First paragraph/);
  assert.equal(c.graphView.arenaKey, 'Arena:other', 'inspection alone cannot change scope');
});

test('an Arena inspector offers a direct entry point to its reachability filter', () => {
  const c = readerTree();
  const arena = { key: 'Arena:devgraph', category: 'arena', kind: 'Arena', id: 'devgraph', title: 'Devgraph', status: 'active' };
  c.detailState.node = arena;
  c.detailState.data = { ...arena, description: 'Development arena', version: 1 };
  c.state.snapshot.graph_nodes = [arena];
  c.graphView.nodes = [arena];
  c.renderGraphSelection();
  const actions = c.document.getElementById('reader-content').children.find(node => node.dataset.uiKey === 'actions');
  const button = actions.children.find(node => node.dataset.action === 'arena-filter');
  assert.equal(button.textContent, 'Filter from this Arena');
});

test('linked document previews render text safely and unresolved references remain explicit', () => {
  const c = readerTree();
  const text = '# Authored plan\n<script>alert("unsafe")</script>\n[link](javascript:alert(1))';
  c.detailState.support = { items: [
    { kind: 'Artifact', id: 'plan', resolution: 'available', metadata: { title: 'Plan', uri: 'file:///configured/plan.md' } },
    { kind: 'Artifact', id: 'dangling', resolution: 'missing', metadata: {} },
  ] };
  c.detailState.documents.set('plan', { content: text });
  c.renderGraphSelection();
  const support = c.document.getElementById('reader-content').children.find(node => node.dataset.uiKey === 'support');
  const preview = support.querySelector('pre');
  assert.equal(preview.textContent, text);
  assert.equal(preview.children.length, 0);
  assert.match(support.textContent, /could not be fully resolved/);
  assert.match(support.textContent, /not an empty document/);
});
