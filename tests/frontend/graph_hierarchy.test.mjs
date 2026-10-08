import assert from 'node:assert/strict';
import test from 'node:test';
import {contextWithFunctions} from './monitor_test_helpers.mjs';
const T=contextWithFunctions([],{}).Topology;
const node=(kind,id)=>({key:`${kind}:${id}`,kind,id,title:id,category:kind==='Arena'?'arena':'work'});
const edge=(a,b,relationship='HAS_CHILD')=>({source:a.key,target:b.key,relationship});
function fixture() {
  const a=node('Arena','a'),b=node('Arena','b'),i=node('Initiative','i'),j=node('Initiative','j');
  const p=node('Project','p'),q=node('Project','q'),r=node('Project','r'),s=node('Proposal','standalone');
  return {a,b,i,j,p,q,r,s,nodes:[a,b,i,j,p,q,r,s],edges:[edge(a,i,'CONTAINS_WORK'),edge(b,j,'CONTAINS_WORK'),edge(i,p),edge(i,q),edge(j,r),edge(p,r,'DEPENDS_ON')]};
}
function overlap(a,b){return a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y;}

test('containment makes disjoint Arena neighborhoods with parents above their siblings',()=>{
  const f=fixture(),h=T.hierarchy(f.nodes,f.edges),positions=h.positions;
  assert.equal(h.arenaOf.get(f.p.key),f.a.key);
  assert.equal(h.arenaOf.get(f.q.key),f.a.key);
  assert.equal(h.arenaOf.get(f.r.key),f.b.key,'cross-Arena dependencies do not change membership');
  assert.equal(h.arenaOf.get(f.s.key),'unassigned');
  assert.equal(positions.get(f.p.key).y,positions.get(f.q.key).y);
  assert.ok(positions.get(f.i.key).y < positions.get(f.p.key).y);
  const boxes=T.regions(h.groups,positions);
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++)assert.equal(overlap(boxes[i],boxes[j]),false);
  assert.deepEqual([...positions], [...T.hierarchy([...f.nodes].reverse(),[...f.edges].reverse()).positions]);
});

test('bounded physics preserves groups, hierarchy, pinned nodes and hidden positions',()=>{
  const f=fixture(),h=T.hierarchy(f.nodes,f.edges),positions=h.positions;
  positions.set('hidden',{x:901,y:800,z:2});
  const first=T.layout(f.nodes,f.edges,positions,{anchors:[...h.positions],iterations:80,pinned:f.p.key});
  const result=new Map(first);
  assert.deepEqual(result.get(f.p.key),positions.get(f.p.key));assert.deepEqual({...result.get('hidden')},positions.get('hidden'));
  assert.ok(result.get(f.i.key).y < result.get(f.p.key).y);
  assert.ok(Math.abs(result.get(f.p.key).y-result.get(f.q.key).y)<=48);
  const boxes=T.regions(h.groups,result);
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++)assert.equal(overlap(boxes[i],boxes[j]),false);
});

test('families occupy real depth bands and 3D relaxation retains them',()=>{
  const f=fixture(),h=T.hierarchy(f.nodes,f.edges);
  assert.ok(h.positions.get(f.i.key).z<h.positions.get(f.p.key).z);
  assert.ok(h.positions.get(f.i.key).z<h.positions.get(f.q.key).z);
  assert.ok(Math.abs(h.positions.get(f.p.key).z-h.positions.get(f.q.key).z)<=64);
  const moved=new Map([...h.positions].map(([k,p])=>[k,{...p,z:p.z+180}]));
  const settled=new Map(T.layout(f.nodes,f.edges,moved,{anchors:[...h.positions],iterations:40}));
  for(const [key,p] of settled)assert.ok(Math.abs(p.z-h.positions.get(key).z)<=24);
  for(const region of T.regions(h.groups,settled)){
    const corners=T.regionCorners(region);
    assert.equal(corners.length,8);assert.equal(new Set(corners.map(p=>p.z)).size,2);
    for(const key of region.keys){const p=settled.get(key);assert.ok(p.z>region.z&&p.z<region.z+region.depth);}
  }
});

test('nodes at different depths draw back to front without changing source order',()=>{
  const nodes=[{key:'near'},{key:'far'},{key:'middle'}],positions=new Map([['near',{depth:-120}],['far',{depth:200}],['middle',{depth:0}]]);
  assert.deepEqual(Array.from(T.depthOrder(nodes,positions),n=>n.key),['far','middle','near']);
  assert.deepEqual(nodes.map(n=>n.key),['near','far','middle']);
});

test('overview filters admit all Work types while excluding observation and receipt categories',()=>{
  for(const value of [{},{work_kind:['Task']},{category:['receipt'],work_kind:['Project','Task']}]) {
    const f=T.overviewFilters(value);
    assert.deepEqual(Array.from(f.category),['arena','work']);
    assert.ok(f.work_kind.every(k=>T.kinds.includes(k)));
    assert.ok(f.work_kind.length);
    assert.equal(f.record_status,null);
  }
  assert.deepEqual(Array.from(T.overviewFilters({work_kind:[]}).work_kind),[],'explicit None remains empty');
  assert.match(T.query(T.overviewFilters()),/work_kind=Project/);
  assert.match(T.query(T.overviewFilters()),/work_kind=Task/);
  assert.doesNotMatch(T.query(T.overviewFilters()),/receipt|observation/);
});

test('250 and 1500 Work fixtures keep each parent family in one neighborhood without overlaps',()=>{
  for(const size of [250,1500]) {
    const nodes=[],edges=[];
    for(let a=0;a<5;a++){
      const arena=node('Arena',String(a));nodes.push(arena);
      for(let p=0;p<5;p++){
        const parent=node('Project',`${a}-${p}`);nodes.push(parent);edges.push(edge(arena,parent,'CONTAINS_WORK'));
        for(let t=0;t<size/25;t++){const task=node('Task',`${a}-${p}-${t}`);nodes.push(task);edges.push(edge(parent,task));}
      }
    }
    const start=performance.now(),h=T.hierarchy(nodes,edges),out=new Map(T.layout(nodes,edges,h.positions,{iterations:40,anchors:[...h.positions]}));
    assert.equal(out.size,nodes.length-5);
    assert.ok([...out.values()].every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)&&Number.isFinite(p.z)));
    for(const e of edges.filter(e=>e.relationship==='HAS_CHILD')){
      assert.equal(h.arenaOf.get(e.source),h.arenaOf.get(e.target));
      assert.ok(out.get(e.source).y<out.get(e.target).y);
    }
    const regions=T.regions(h.groups,out);
    for(let i=0;i<regions.length;i++)for(let j=i+1;j<regions.length;j++)assert.equal(overlap(regions[i],regions[j]),false);
    assert.ok(performance.now()-start<5000,'bounded layout completes within a generous 5-second test budget');
  }
});

test('missing parents, cycles, and empty Arenas terminate with a deterministic finite layout',()=>{
  const a=node('Project','a'),b=node('Project','b'),empty=node('Arena','empty');
  const h=T.hierarchy([a,b,empty],[edge(a,b),edge(b,a),{source:'missing',target:a.key,relationship:'HAS_CHILD'}]);
  assert.equal(h.positions.size,2);assert.equal(h.groups.length,2);
  assert.ok([...h.positions.values()].every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
});

function workFamily() {
  const a=node('Arena','a'),i=node('Initiative','i'),p=node('Project','p'),other=node('Project','other');
  const issue=node('Issue','child'),task=node('Task','grandchild'),direct=node('Task','direct');
  const rootIssue=node('Issue','root'),rootTask=node('Task','root'),unrelated=node('Task','unrelated');
  const nodes=[a,i,p,other,issue,task,direct,rootIssue,rootTask,unrelated];
  const edges=[edge(a,i,'CONTAINS_WORK'),edge(a,rootIssue,'CONTAINS_WORK'),edge(i,p),edge(p,issue),edge(issue,task),edge(i,direct),edge(other,unrelated),edge(p,unrelated,'DEPENDS_ON')];
  return {a,i,p,other,issue,task,direct,rootIssue,rootTask,unrelated,nodes,edges};
}
const keys=projection=>Array.from(projection.nodes,n=>n.key).sort();

test('overview includes parentless Issues and Tasks even when assigned to an Arena',()=>{
  const f=workFamily(),view=T.overviewProjection(f.nodes,f.edges);
  assert.deepEqual(keys(view),[f.a,f.i,f.p,f.other,f.rootIssue,f.rootTask].map(n=>n.key).sort());
  assert.equal(view.context.nodes.some(n=>n.key===f.task.key),false,'collapsed descendants do not consume layout space');
});

test('hover reveals one level; moving into a child retains the path without opening sibling branches',()=>{
  const f=workFamily(),context={nodes:f.nodes,edges:f.edges};
  const initiative=T.overviewProjection(f.nodes,f.edges,context,[f.i.key]);
  assert.ok(keys(initiative).includes(f.direct.key));
  assert.ok([f.issue,f.task].every(n=>!keys(initiative).includes(n.key)),'grandchildren stay hidden');
  assert.ok(!keys(initiative).includes(f.unrelated.key),'dependencies do not expand unrelated children');
  const project=T.overviewProjection(f.nodes,f.edges,context,[f.p.key]);
  assert.ok(keys(project).includes(f.issue.key));
  assert.ok(!keys(project).includes(f.task.key));
  assert.ok(!keys(project).includes(f.direct.key));
  const issue=T.overviewProjection(f.nodes,f.edges,context,[f.issue.key]);
  assert.ok([f.issue,f.task].every(n=>keys(issue).includes(n.key)));
  assert.ok(!keys(issue).includes(f.direct.key));
  assert.equal(T.attentionRoot(f.p.key,f.i.key,initiative),f.p.key,'a child parent opens the next level');
  assert.equal(T.attentionRoot(f.task.key,f.i.key,initiative),f.issue.key);
  assert.equal(T.attentionRoot(f.task.key,null,initiative),f.issue.key,'reading a leaf retains its immediate parent');
  assert.equal(T.attentionRoot(f.other.key,f.i.key,initiative),f.other.key);
  assert.equal(T.attentionRoot(f.rootTask.key,f.i.key,initiative),null);
});

test('filtered ancestors still classify children, and expansion respects filters and hidden connections',()=>{
  const f=workFamily(),context={nodes:f.nodes,edges:f.edges};
  const loaded=f.nodes.filter(n=>![f.issue.key,f.i.key].includes(n.key));
  const collapsed=T.overviewProjection(loaded,[],context);
  assert.ok(!keys(collapsed).includes(f.direct.key),'hidden parent does not make a Task top-level');
  const expanded=T.overviewProjection(loaded,[],context,[f.p.key]);
  assert.ok(!keys(expanded).includes(f.task.key),'one-level reveal does not skip a filtered intermediate parent');
  assert.ok(!keys(expanded).includes(f.issue.key),'filtered records stay hidden');
  assert.equal(expanded.edges.length,0,'hidden connection types stay hidden');
  const leaf=T.overviewProjection(loaded,[],context,[f.task.key]);
  assert.ok(!keys(leaf).includes(f.task.key),'a leaf is not an expansion root');
});

test('descendant traversal terminates on cycles and ignores missing expansion roots',()=>{
  const f=workFamily(),edges=[...f.edges,edge(f.task,f.p)];
  const view=T.overviewProjection(f.nodes,edges,{nodes:f.nodes,edges},[f.p.key,'missing']);
  assert.equal(view.families.size,1);
  assert.equal(view.families.get(f.p.key).size,2);
  assert.equal(T.attentionRoot(f.task.key,'missing',view),f.task.key);
});

test('250 and 1500 revealed children preserve all overview anchors and their local depth bands',()=>{
  for(const size of [250,1500]){
    const f=fixture(),all=[...f.nodes],edges=[...f.edges];
    for(let i=0;i<size;i++){
      const child=node('Task',`child-${i}`);all.push(child);edges.push(edge(i%2?f.p:f.r,child));
    }
    const overview=T.hierarchy(f.nodes,f.edges),anchors=new Map(overview.positions);
    anchors.set(f.p.key,{x:500,y:210,z:350});
    anchors.set(f.r.key,{x:-750,y:-100,z:500});
    const start=performance.now(),full=T.hierarchy(all,edges),revealed=T.anchorHierarchy(full,anchors);
    for(const [key,p] of anchors)assert.deepEqual({...revealed.positions.get(key)},{...p});
    for(const link of edges.filter(e=>e.target.startsWith('Task:'))){
      const p=revealed.positions.get(link.source),child=revealed.positions.get(link.target);
      assert.ok(child.y>p.y&&child.z>p.z,'children remain behind and below their own anchored parent');
    }
    assert.equal(revealed.positions.size,all.length-2);
    assert.ok(performance.now()-start<2000,'reveal remains bounded without running global physics');
  }
});
