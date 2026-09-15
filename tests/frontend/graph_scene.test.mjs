import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions, treeDocument } from './monitor_test_helpers.mjs';

function scene() {
  const document = treeDocument();
  const c = contextWithFunctions([
    'renderGraph', 'setSvg', 'nodeShape', 'relationshipLabel', 'neighborhoodKeys',
    'chooseGraphLabels', 'reconcileChildren',
  ], {
    document,
    state: { snapshot: { graph_nodes: [] }, selectedGraphKey: null },
    graphView: { width: 800, height: 420, labels: 'none', nodes: [], edges: [] },
    ensureGraphScene() {},
    text(id, value) { document.getElementById(id).textContent = value; },
    graphCoordinates(nodes) { return new Map(nodes.map(node => [node.key, { x: 100, y: 100, radius: 18 }])); },
    projectGraphPoint(point) { return point; },
    svgMake(tag, attributes = {}) {
      const node = document.createElement(tag);
      for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
      return node;
    },
  });
  return c;
}

test('removing a focused SVG node returns keyboard focus to the graph canvas', () => {
  const c = scene();
  const node = { key: 'Task:a', kind: 'Task', category: 'work', id: 'a', title: 'Removed task', status: 'draft' };
  c.state.snapshot.graph_nodes = [node];
  c.renderGraph([node], []);
  const element = c.document.getElementById('scene-nodes').children[0];
  element.focus();
  c.state.snapshot.graph_nodes = [];
  c.renderGraph([], []);
  assert.equal(element.parentNode, null);
  assert.equal(c.document.activeElement, c.document.getElementById('graph-svg'));
});

test('unchanged SVG nodes retain identity and focus across projection updates', () => {
  const c = scene();
  const node = { key: 'Task:a', kind: 'Task', category: 'work', id: 'a', title: 'Original title', status: 'draft' };
  c.state.snapshot.graph_nodes = [node];
  c.renderGraph([node], []);
  const element = c.document.getElementById('scene-nodes').children[0];
  element.focus();
  c.renderGraph([{ ...node, title: 'Revised title', status: 'review' }], []);
  assert.equal(c.document.getElementById('scene-nodes').children[0], element);
  assert.equal(c.document.activeElement, element);
  assert.match(element.getAttribute('aria-label'), /Revised title, review/);
});
