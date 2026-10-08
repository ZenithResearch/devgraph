import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { contextWithFunctions } from './monitor_test_helpers.mjs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const tokens = read('src/devgraph/frontend/static/topology/zenith-tokens.css');
const pin = JSON.parse(read('docs/dev/zenith-tokens.json'));

test('packaged Zenith tokens match the pinned source and include every advertised theme', () => {
  assert.equal(createHash('sha256').update(tokens).digest('hex'), pin.sha256);
  assert.deepEqual([...new Set([...tokens.matchAll(/\[data-theme="(\w+)"\]/g)].map(match => match[1]))], pin.themes);
  for (const page of ['src/devgraph/frontend/app.py', 'src/devgraph/frontend/static/selection/page/index.html']) {
    const html = read(page);
    const bootstrap = html.indexOf('<script src="/monitor/topology-assets/theme.js"></script>');
    assert.ok(bootstrap > 0 && bootstrap < html.indexOf('<body>'), 'restore preference before first paint');
    assert.ok(html.includes('/monitor/topology-assets/zenith-tokens.css'));
    assert.ok(html.includes('/monitor/topology-assets/theme.css'));
    assert.ok(html.includes('data-theme-picker'));
    for (const theme of [...pin.themes, 'system']) assert.ok(html.includes(`<option value="${theme}">`));
    assert.ok(!html.includes('color-scheme: dark'), 'the shared tokens own color scheme');
  }
});

test('theme repaint resolves the shared roles once and only repaints existing graph state', () => {
  const nodes = [{key:'Task:a'}], edges = [], positions = new Map([['Task:a',{x:17,y:42,z:0}]]);
  const graphView = {nodes, edges, nodePositions:positions, zoom:1.8, filters:{q:'keep'}, history:['keep']};
  const state = {selectedGraphKey:'Task:a'};
  let reads = 0, renders = 0;
  const c = contextWithFunctions(['repaintGraphTheme'], {
    document:{documentElement:{}}, graphView, state,
    getComputedStyle() { reads++; return {getPropertyValue:name=>` ${name}-resolved `}; },
    renderGraph(nextNodes, nextEdges) { renders++; assert.equal(nextNodes,nodes); assert.equal(nextEdges,edges); },
    fetch() { assert.fail('theme changes must not request graph data'); },
  });
  c.repaintGraphTheme(); c.repaintGraphTheme();
  assert.equal(reads,2); assert.equal(renders,2);
  assert.equal(graphView.paint.labelBackground,'--canvas-resolved');
  assert.equal(graphView.paint.nodeBorder,'--graph-node-border-resolved');
  assert.equal(graphView.nodePositions,positions);
  assert.equal(graphView.zoom,1.8);
  assert.deepEqual(graphView.filters,{q:'keep'});
  assert.deepEqual(graphView.history,['keep']);
  assert.equal(state.selectedGraphKey,'Task:a');
});
