import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions, textDocument } from './monitor_test_helpers.mjs';

function model(names=[]) {
  return contextWithFunctions(names, {
    document:textDocument(), state:{snapshot:{complete:true},selectedGraphKey:null},
    graphView:{nodePositions:new Map([['Task:a',{x:10,y:20,z:0}],['Task:b',{x:80,y:20,z:0}]]),nodeVelocities:new Map(),edgeStrengths:new Map(),repulsionStrength:1.5,pinnedKey:null,nodes:[{key:'Task:a'},{key:'Task:b'}],edges:[],history:[],layoutGeneration:0,yaw:0,pitch:0,zoom:1,panX:0,panY:0},
    pauseGraphOrbit(){},renderForceControls(){},syncZoomControls(){},requestGraphRender(){},renderGraph(){},announceGraph(){},
  });
}

test('the visual registry gives all eight types different shapes and colors',()=>{
  const {Topology:T}=model(), entries=Object.values(T.registry);
  assert.equal(entries.length,8);assert.equal(new Set(entries.map(v=>v.color)).size,8);
  assert.equal(new Set(Object.keys(T.registry).map(k=>T.shape(k,12))).size,8);
  assert.equal(T.visual('EventReceipt').label,'Record');
});

test('Work types use all five regular, closed Platonic solids',()=>{
  const {Topology:T}=model();
  assert.equal(new Set(T.kinds.map(kind=>T.visual(kind).shape)).size,5);
  const expected={tetrahedron:[4,4,3],cube:[8,6,4],octahedron:[6,8,3],dodecahedron:[20,12,5],icosahedron:[12,20,3]};
  for(const [name,[vertices,faces,sides]] of Object.entries(expected)) {
    const mesh=T.solids[name],edges=new Map(),lengths=[];
    assert.equal(mesh.vertices.length,vertices);assert.equal(mesh.faces.length,faces);
    for(const face of mesh.faces) {
      assert.equal(face.indices.length,sides);
      for(let i=0;i<sides;i++) {
        const a=face.indices[i],b=face.indices[(i+1)%sides],key=[a,b].sort((a,b)=>a-b).join(',');
        edges.set(key,(edges.get(key)||0)+1);
        lengths.push(Math.hypot(...mesh.vertices[a].map((v,j)=>v-mesh.vertices[b][j])));
      }
    }
    assert.ok([...edges.values()].every(count=>count===2),'each edge borders two faces');
    assert.equal(vertices-edges.size+faces,2,'closed convex surface');
    assert.ok(Math.max(...lengths)-Math.min(...lengths)<1e-7,'all edges have equal length');
  }
});

test('projected solids share cached, finite artwork bounded by the picking radius',()=>{
  const {Topology:T}=model();
  for(const kind of [...Object.keys(T.registry),'unknown','constructor']) {
    const art=T.solid(kind);
    assert.equal(art,T.solid(kind));assert.ok(art.faces.length>=2);
    assert.ok(art.outline.every(p=>Math.hypot(p[0],p[1])<=1+1e-7));
    for(const face of art.faces){assert.doesNotMatch(face.d,/NaN|Infinity/);assert.match(face.fill,/^#[0-9a-f]{6}$/);}
  }
});

test('saved preferences preserve explicit none, remove obsolete values and exclude private fields',()=>{
  const {Topology:T}=model();
  const p=T.preferences({version:1,filters:{work_kind:['Task','Obsolete'],category:[],q:'  TITLE  '},token:'secret',nodes:[{description:'secret'}],readerWidth:900});
  assert.equal(T.query(p.filters),'category=none&q=title&work_kind=Task');
  assert.equal(p.readerWidth,600);assert.doesNotMatch(JSON.stringify(p),/secret|Obsolete/);
  assert.equal(T.query(T.preferences({version:999,filters:{category:[]}}).filters),'');
});

test('worker layout is deterministic, finite, nonmutating, and preserves pinned and hidden positions',()=>{
  const {Topology:T}=model();
  const nodes=Array.from({length:1500},(_,i)=>({key:`Task:${i}`,kind:'Task'}));
  const positions=T.coordinates(nodes),original=JSON.stringify([...positions]);
  const edges=nodes.slice(1).map((n,i)=>({source:nodes[i].key,target:n.key,relationship:'HAS_CHILD'}));
  const first=T.layout(nodes,edges,positions,{iterations:3,pinned:'Task:1'});
  assert.equal(JSON.stringify([...positions]),original);
  assert.equal(JSON.stringify(first),JSON.stringify(T.layout(nodes,edges,positions,{iterations:3,pinned:'Task:1'})));
  assert.deepEqual(new Map(first).get('Task:1'),positions.get('Task:1'));
  assert.ok(first.every(([,p])=>Number.isFinite(p.x)&&Number.isFinite(p.y)&&Number.isFinite(p.z)));
});

const historyFunctions=['invalidateLayout','beginArrangement','finishArrangement','cancelArrangement','syncUndoControl','undoPositioning'];
test('a positioning gesture is one undo entry and undo preserves later arrivals',()=>{
  const c=model(historyFunctions),v=c.graphView;v.filters=c.Topology.filters();
  c.beginArrangement('Move item');v.nodePositions.get('Task:a').x=500;c.beginArrangement('Repeated key');v.nodePositions.get('Task:a').x=800;c.finishArrangement();
  assert.equal(v.history.length,1);
  v.nodes.push({key:'Task:new'});v.nodePositions.set('Task:new',{x:900,y:20,z:0});
  c.undoPositioning();assert.equal(v.nodePositions.get('Task:a').x,10);assert.equal(v.nodePositions.get('Task:new').x,900);
  assert.equal(v.history.length,0);
});
test('cancelling a gesture restores positions without creating history; history is bounded',()=>{
  const c=model(historyFunctions),v=c.graphView;
  c.beginArrangement('Move');v.nodePositions.get('Task:a').x=999;c.cancelArrangement();
  assert.equal(v.nodePositions.get('Task:a').x,10);assert.equal(v.history.length,0);
  for(let i=0;i<30;i++){c.beginArrangement('Move');v.nodePositions.get('Task:a').x++;c.finishArrangement();}
  assert.equal(v.history.length,20);
});
test('undo does not resurrect deleted nodes but restores hidden positions in a filtered view',()=>{
  const c=model(historyFunctions),v=c.graphView;v.filters=c.Topology.filters();
  c.beginArrangement('Move');v.nodePositions.get('Task:b').x=999;c.finishArrangement();v.nodes=[{key:'Task:a'}];v.nodePositions.delete('Task:b');c.undoPositioning();assert.equal(v.nodePositions.has('Task:b'),false);
  v.filters=c.Topology.filters({work_kind:['Issue']});v.nodePositions.set('Task:b',{x:80,y:20,z:0});c.beginArrangement('Move');v.nodePositions.get('Task:b').x=999;c.finishArrangement();c.undoPositioning();assert.equal(v.nodePositions.get('Task:b').x,80);
});
test('late worker replies cannot overwrite a newer gesture or filter generation',()=>{
  const c=model(['invalidateLayout','relaxGraph']);const workers=[];
  c.Worker=class {constructor(){workers.push(this);}postMessage(value){this.message=value;}terminate(){this.terminated=true;}};
  c.graphCoordinates=()=>c.graphView.nodePositions;
  c.relaxGraph();const first=workers[0];c.invalidateLayout();
  first.onmessage({data:{generation:first.message.generation,positions:[['Task:a',{x:999,y:0,z:0}]]}});
  assert.equal(c.graphView.nodePositions.get('Task:a').x,10);assert.equal(first.terminated,true);
  c.relaxGraph();const next=workers[1];next.onmessage({data:{generation:next.message.generation,positions:[['Task:a',{x:30,y:0,z:0}]]}});
  assert.equal(c.graphView.nodePositions.get('Task:a').x,30);assert.equal(c.graphView.worker,null);
});

test('canonical UI query encoding matches the fixtures consumed by the API', async()=>{
  const {readFileSync}=await import('node:fs'),{Topology:T}=model();
  const fixtures=JSON.parse(readFileSync(new URL('./fixtures/topology_queries.json',import.meta.url),'utf8'));
  for(const fixture of fixtures)assert.equal(T.query(fixture.input),fixture.query);
  assert.equal(T.query(T.preferences({version:1,filters:null}).filters),'');
});
