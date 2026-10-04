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

test('overview filters migrate older broad or Task-only preferences without rendering detailed records',()=>{
  for(const value of [{},{work_kind:['Task']},{category:['receipt'],work_kind:['Project','Task']}]) {
    const f=T.overviewFilters(value);
    assert.deepEqual(Array.from(f.category),['arena','work']);
    assert.ok(f.work_kind.every(k=>['Proposal','Initiative','Project'].includes(k)));
    assert.ok(f.work_kind.length);
    assert.equal(f.record_status,null);
  }
  assert.deepEqual(Array.from(T.overviewFilters({work_kind:[]}).work_kind),[],'explicit None remains empty');
  assert.match(T.query(T.overviewFilters()),/work_kind=Project/);
  assert.doesNotMatch(T.query(T.overviewFilters()),/Task|Issue|receipt|observation/);
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
    assert.ok([...out.values()].every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.z===0));
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
