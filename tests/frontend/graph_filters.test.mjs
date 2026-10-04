import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { topologySource } from './monitor_test_helpers.mjs';

const frontend = readFileSync(new URL('../../src/devgraph/frontend/app.py', import.meta.url), 'utf8');
const names = [
  'graphLane', 'stableGraphDepth', 'baseGraphCoordinates', 'syncGraphPhysics',
  'filterGraph', 'filterGraphByArena', 'neighborhoodKeys', 'updateGraphVisibility', 'render', 'refresh',
  'synchronizeCredential', 'applySnapshot', 'setGraphAttention',
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
      preferenceKey: 'fixture', filters: {}, filterSerial: 0, visibleCategories: new Set(categories), nodes: [], edges: [],
      nodePositions: new Map(), nodeVelocities: new Map(), pointers: new Map(), pinnedKey: null, fitted: false,
    },
    tokenInput: { value: 'synthetic-test-credential' },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { className: '', textContent: '' });
      return elements.get(id);
    } },
    topologyPath: () => '/monitor/topology/v1', renderTopologyControls() {}, text() {}, relativeTime() { return 'now'; }, pauseGraphOrbit() {}, invalidateLayout() {}, requestGraphRender() {},
    renderPipeline() {}, renderBars() {}, renderActivity() {}, renderObservations() {},
    renderForceControls() {}, renderArenaFilter() {}, relaxGraph() {}, fitGraph() {},
    renderGraph() {}, renderGraphSelection() {}, renderGraphSearch() {}, loadSelectedDetail() {}, queueMicrotask,
  });
  c.relaxations = 0;
  c.relaxGraph = () => { c.relaxations += 1; };
  vm.runInContext(topologySource, c); c.Topology = c.DevgraphTopology;
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
  c.getJson = async path => path === '/monitor/topology/v1' ? next : { items: [] };
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
  assert.deepEqual(Array.from(c.graphView.nodes, node => node.key), ['work-a']);
  assert.deepEqual(Array.from(c.graphView.edges, edge => [edge.source, edge.target]), []);
});

test('hover reveal anchors the parent, preserves the camera, and collapse restores the overview',()=>{
  const c=monitor(); c.state.snapshot=snapshot(); c.updateGraphVisibility();
  const position={x:317,y:-54,z:106}; c.graphView.nodePositions.set('work-a',position);
  c.graphView.panX=73; c.graphView.zoom=1.7;
  c.fitGraph=()=>assert.fail('hover must not refit the camera');
  c.setGraphAttention('hoveredKey','work-a');
  assert.deepEqual(Array.from(c.graphView.nodes,n=>n.key),['work-a','work-b']);
  assert.deepEqual({...c.graphView.nodePositions.get('work-a')},position);
  const child={...c.graphView.nodePositions.get('work-b')};
  c.updateGraphVisibility();
  assert.deepEqual({...c.graphView.nodePositions.get('work-b')},child,'refresh preserves the expanded arrangement');
  assert.deepEqual({...c.graphView.layoutPlan.positions.get('work-a')},position,'refresh keeps layout anchors aligned');
  c.graphView.projected=new Map([['work-a',{x:100,y:100}],['work-b',{x:200,y:200}]]);
  c.setGraphAttention('hoveredKey',null,{x:150,y:150});
  assert.equal(c.graphView.hoverRoot,'work-a','crossing the space between parent and child keeps the family open');
  c.setGraphAttention('hoveredKey','work-b');
  assert.equal(c.graphView.hoverRoot,'work-a');
  c.setGraphAttention('hoveredKey',null);
  assert.deepEqual(Array.from(c.graphView.nodes,n=>n.key),['work-a']);
  assert.deepEqual({...c.graphView.nodePositions.get('work-a')},position);
  assert.equal(c.graphView.panX,73); assert.equal(c.graphView.zoom,1.7);
});

test('keyboard and selected families survive pointer exit and ignore internal focus transfers',()=>{
  const c=monitor(); c.state.snapshot=snapshot(); c.updateGraphVisibility();
  c.setGraphAttention('focusedKey','work-a');
  c.setGraphAttention('hoveredKey','work-a'); c.setGraphAttention('hoveredKey',null);
  assert.equal(c.graphView.nodes.length,2);
  c.graphView.movingFocus=true; c.setGraphAttention('focusedKey',null); c.graphView.movingFocus=false;
  assert.equal(c.graphView.focusRoot,'work-a');
  c.graphView.selectedRoot='work-a'; c.setGraphAttention('focusedKey',null);
  assert.equal(c.graphView.nodes.length,2,'reading in the sidebar retains the selected family');
  c.graphView.selectedRoot=null; c.updateGraphVisibility(true);
  assert.equal(c.graphView.nodes.length,1);
});

test('refresh classifies changed parentage without restoring a stale collapsed layout',()=>{
  const c=monitor(); c.state.snapshot=snapshot(); c.updateGraphVisibility();
  c.setGraphAttention('hoveredKey','work-a');
  c.state.snapshot=snapshot(nodes,[]); c.updateGraphVisibility();
  c.setGraphAttention('hoveredKey',null);
  assert.deepEqual(Array.from(c.graphView.nodes,n=>n.key),['work-a','work-b'],'newly standalone Issue remains visible after collapse');
  for(const node of c.graphView.nodes) assert.deepEqual({...c.graphView.nodePositions.get(node.key)},{...c.graphView.layoutPlan.positions.get(node.key)});
});

test('revealing, switching, refreshing, and collapsing families keeps every existing node and the camera still',()=>{
  const c=monitor();
  const work=(kind,id)=>({key:`${kind}:${id}`,id,kind,category:kind==='Arena'?'arena':'work'});
  const a=work('Arena','a'),b=work('Arena','b'),i=work('Initiative','i'),p=work('Project','p'),q=work('Project','q');
  const issue=work('Issue','child'),task=work('Task','nested'),other=work('Task','other'),standalone=work('Issue','standalone');
  const link=(source,target,relationship='HAS_CHILD')=>({source:source.key,target:target.key,relationship});
  c.state.snapshot=snapshot([a,b,i,p,q,issue,task,other,standalone],[link(a,i,'CONTAINS_WORK'),link(b,q,'CONTAINS_WORK'),link(i,p),link(p,issue),link(issue,task),link(q,other)]);
  c.updateGraphVisibility();
  const camera={panX:73,panY:-48,zoom:1.7,yaw:-.32,pitch:.24,cameraDistance:5000,fitted:true};
  Object.assign(c.graphView,camera);
  const original=new Map([...c.graphView.nodePositions].map(([k,p])=>[k,{...p}]));
  c.fitGraph=()=>assert.fail('attention must not refit');
  c.relaxGraph=()=>assert.fail('attention must not run global physics');
  const still=action=>{
    const before=new Map(c.graphView.nodes.map(n=>[n.key,{...c.graphView.nodePositions.get(n.key)}]));
    action();
    for(const n of c.graphView.nodes)if(before.has(n.key))assert.deepEqual({...c.graphView.nodePositions.get(n.key)},before.get(n.key),`${n.key} must not move`);
    for(const [k,v] of Object.entries(camera))assert.equal(c.graphView[k],v,`camera ${k} must not move`);
  };
  for(let cycle=0;cycle<3;cycle++){
    still(()=>c.setGraphAttention('hoveredKey',i.key));
    assert.ok(c.graphView.nodes.some(n=>n.key===task.key),'all descendants are still revealed');
    still(()=>c.setGraphAttention('focusedKey',q.key));
    assert.ok(c.graphView.nodes.some(n=>n.key===other.key),'two independently anchored families can be open');
    still(()=>c.setGraphAttention('hoveredKey',q.key));
    still(()=>c.updateGraphVisibility());
    still(()=>c.setGraphAttention('hoveredKey',null));
    still(()=>c.setGraphAttention('focusedKey',null));
    for(const [key,position] of original)assert.deepEqual({...c.graphView.nodePositions.get(key)},position);
  }
});

test('collapsing does not undo manual positioning and reopened children follow a moved parent',()=>{
  const c=monitor();c.state.snapshot=snapshot();c.updateGraphVisibility();
  c.setGraphAttention('hoveredKey','work-a');
  c.graphView.nodePositions.set('work-a',{x:900,y:30,z:120});
  c.setGraphAttention('hoveredKey',null);
  assert.deepEqual({...c.graphView.nodePositions.get('work-a')},{x:900,y:30,z:120});
  c.setGraphAttention('hoveredKey','work-a');
  const first={...c.graphView.nodePositions.get('work-b')};
  c.setGraphAttention('hoveredKey',null);
  c.graphView.nodePositions.set('work-a',{x:1080,y:80,z:180});
  c.setGraphAttention('hoveredKey','work-a');
  assert.deepEqual({...c.graphView.nodePositions.get('work-b')},{x:first.x+180,y:first.y+50,z:first.z+60});
});
