import assert from 'node:assert/strict';
import test from 'node:test';
import {contextWithFunctions} from './monitor_test_helpers.mjs';

const T=contextWithFunctions([],{}).Topology;
const camera={yaw:-.32,pitch:.24,zoom:.19,panX:0,panY:0,width:790,height:420,cameraDistance:5000};

test('local reveal separates crowded children in screen space without moving parents or other neighborhoods',()=>{
  const nodes=['parent','neighbor',...Array.from({length:24},(_,i)=>`child-${i}`)].map(key=>({key}));
  const positions=new Map(nodes.map((n,i)=>[n.key,{x:i*3,y:i*2,z:i*40}]));
  positions.set('neighbor',{x:350,y:50,z:-80});
  const before=JSON.stringify([...positions]),movable=nodes.slice(2).map(n=>n.key),parents=movable.map(key=>[key,'parent']);
  const settled=new Map(T.revealLayout(nodes,positions,{camera,movable,parents}));
  assert.equal(JSON.stringify([...positions]),before,'worker input is immutable');
  for(const key of ['parent','neighbor'])assert.deepEqual({...settled.get(key)},positions.get(key));
  const projected=nodes.map(n=>({key:n.key,...T.projectPoint(settled.get(n.key),camera)}));
  for(const child of projected.filter(p=>movable.includes(p.key))){
    const before=T.projectPoint(positions.get(child.key),camera);
    assert.ok(Math.abs(child.depth-before.depth)<1e-6,'screen repulsion preserves the depth band');
    for(const other of projected)if(other.key!==child.key)assert.ok(Math.hypot(child.x-other.x,child.y-other.y)>24,`${child.key} needs a readable target separate from ${other.key}`);
  }
});

test('local reveal handles 250 and 1500 children in a bounded worker pass',()=>{
  for(const count of [250,1500]){
    const nodes=[{key:'parent'},...Array.from({length:count},(_,i)=>({key:`child-${i}`}))];
    const positions=new Map(nodes.map((n,i)=>[n.key,{x:i%5,y:Math.floor(i/5),z:200+i%3*30}]));
    const movable=nodes.slice(1).map(n=>n.key),started=performance.now();
    const result=new Map(T.revealLayout(nodes,positions,{camera,movable,parents:movable.map(key=>[key,'parent'])}));
    assert.equal(result.size,count+1);
    assert.deepEqual({...result.get('parent')},positions.get('parent'));
    assert.ok([...result.values()].every(p=>Object.values(p).every(Number.isFinite)));
    assert.ok(performance.now()-started<2500,'bounded collision grid completes in under 2.5 seconds');
  }
});

function workerMonitor(reducedMotion=false){
  const workers=[],frames=new Map();let serial=0;
  const c=contextWithFunctions(['settleRevealedChildren','invalidateLayout'],{
    Worker:class{constructor(){workers.push(this);}postMessage(data){this.data=data;}terminate(){this.terminated=true;}},
    window:{matchMedia:()=>({matches:reducedMotion}),requestAnimationFrame(fn){frames.set(++serial,fn);return serial;},cancelAnimationFrame(id){frames.delete(id);}},
    graphView:{...camera,layoutGeneration:0,nodes:[{key:'parent'},{key:'child'}],nodePositions:new Map([['parent',{x:0,y:0,z:0}],['child',{x:0,y:40,z:150}]]),pointers:new Map(),layoutPlan:{parents:new Map([['child','parent']]),positions:new Map()},revealCache:new Map()},
    graphCoordinates:()=>c.graphView.nodePositions,renderGraph(){},
  });
  const deliver=worker=>worker.onmessage({data:{generation:worker.data.generation,positions:[['parent',{x:0,y:0,z:0}],['child',{x:300,y:100,z:160}]]}});
  const frame=now=>{const pending=[...frames.values()];frames.clear();for(const fn of pending)fn(now);};
  return {c,workers,frames,deliver,frame};
}

test('reveal physics eases only children, caches the settled result and honors reduced motion',()=>{
  for(const reduced of [false,true]){
    const {c,workers,deliver,frame}=workerMonitor(reduced);
    c.settleRevealedChildren(['child']);deliver(workers[0]);frame(0);
    if(!reduced){frame(120);assert.ok(c.graphView.nodePositions.get('child').x>0&&c.graphView.nodePositions.get('child').x<300);frame(240);}
    assert.deepEqual({...c.graphView.nodePositions.get('child')},{x:300,y:100,z:160});
    assert.deepEqual(c.graphView.nodePositions.get('parent'),{x:0,y:0,z:0});
    assert.deepEqual({...c.graphView.revealCache.get('child').position},{x:300,y:100,z:160});
  }
});

test('stale workers and in-flight animation cannot move a collapsed branch or a changed camera',()=>{
  const {c,workers,frames,deliver,frame}=workerMonitor();
  c.settleRevealedChildren(['child']);c.invalidateLayout();deliver(workers[0]);
  assert.equal(frames.size,0);
  c.settleRevealedChildren(['child']);c.graphView.zoom=.4;deliver(workers[1]);
  assert.equal(frames.size,0);
  c.settleRevealedChildren(['child']);deliver(workers[2]);frame(0);frame(120);
  const position={...c.graphView.nodePositions.get('child')};c.invalidateLayout();frame(240);
  assert.deepEqual({...c.graphView.nodePositions.get('child')},position);
});
