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
    ensureGraphScene() {}, updateGraphPreview() {},
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
  const solid = element.querySelector('.graph-solid');
  assert.equal(solid.getAttribute('href'), '#graph-solid-Task');
  element.focus();
  c.renderGraph([{ ...node, title: 'Revised title', status: 'review', todo_progress: 'in_progress' }], []);
  assert.equal(c.document.getElementById('scene-nodes').children[0], element);
  assert.equal(element.querySelector('.graph-solid'), solid);
  assert.equal(c.document.activeElement, element);
  assert.match(element.getAttribute('aria-label'), /Revised title, In progress/);
});

test('expansion into Canvas keeps one focused node control and restores SVG without losing focus',()=>{
  const c=scene();
  const parent={key:'Project:p',kind:'Project',category:'work',title:'Parent',status:'draft'};
  const children=Array.from({length:250},(_,i)=>({key:`Task:${i}`,kind:'Task',category:'work',title:`Child ${i}`,status:'draft'}));
  c.DevgraphTopologyCanvas={draw(){return [];}};
  c.renderGraph([parent],[]);
  const element=c.document.getElementById('scene-nodes').children[0];
  element.closest=selector=>selector==='.graph-node'?element:null; element.focus();
  c.renderGraph([parent,...children],[]);
  assert.equal(c.graphView.canvasMode,true);
  assert.equal(c.document.activeElement,element);
  assert.equal(c.document.getElementById('scene-nodes').children.length,1,'Canvas retains only the focused accessibility control');
  c.renderGraph([parent],[]);
  assert.equal(c.graphView.canvasMode,false);
  assert.equal(c.document.activeElement,element);
  assert.equal(c.document.getElementById('scene-nodes').children[0],element);
});
