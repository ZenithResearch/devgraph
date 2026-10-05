import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {contextWithFunctions, treeDocument} from './monitor_test_helpers.mjs';
const {briefing} = createRequire(import.meta.url)('../../src/devgraph/frontend/static/topology/check-in.js');
const node = (id, kind = 'Project', extra = {}) => ({key:`${kind}:${id}`, id, kind, title:id, category:'work', status:'draft', archived:false, ...extra});
const snapshot = (nodes, edges = []) => ({graph_nodes:nodes, graph_edges:edges});

test('briefing excludes archived work, deduplicates and never maps legacy lifecycle to progress or review', () => {
  const active = node('active', 'Task', {todo_progress:'in_progress', workflow:{column:'review',label:'Layer review'}});
  const report = briefing(snapshot([node('draft'), node('accepted','Initiative',{status:'accepted',progress:{percent:100}}),
    node('review','Project',{status:'review'}), node('archived','Project',{archived:true,todo_progress:'in_progress'}), active, active]));
  assert.equal(report.unclassified,3);
  assert.equal(report.started,1);
  assert.equal(report.reviewing,1);
  assert.equal(report.projectTotal,3);
  assert.deepEqual(report.attention.map(row=>row.reason),['Layer review']);
});

test('dependency directions, deduplication and completion use canonical progress; archiving never resolves prerequisites', () => {
  const nodes = [node('parent'), node('a','Task',{archived:true}), node('b','Task',{todo_progress:'done'}), node('c','Task',{status:'accepted'}), node('done','Project',{todo_progress:'done'})];
  const report = briefing(snapshot(nodes,[
    {source:'Project:parent',target:'Task:a',relationship:'DEPENDS_ON'},
    {source:'Task:c',target:'Project:parent',relationship:'BLOCKS'},
    {source:'Project:parent',target:'Task:c',relationship:'DEPENDS_ON'},
    {source:'Project:parent',target:'Task:b',relationship:'DEPENDS_ON'},
    {source:'Project:done',target:'Task:a',relationship:'DEPENDS_ON'},
    {source:'Project:parent',target:'Task:a',relationship:'HAS_CHILD'},
  ]));
  assert.equal(report.dependencies,1);
  assert.equal(report.attentionTotal,1);
  assert.equal(report.attention[0].reason,'Check dependency: a · Check dependency: c');
  assert.equal(report.projects[0].id,'parent');
});

test('review and waiting use explicit stage with active progress, including plan approval', () => {
  const report = briefing(snapshot([
    node('approval','Proposal',{todo_progress:'in_progress',workflow:{column:'plan_approval',label:'CEO plan approval'}}),
    node('waiting','Task',{todo_progress:'in_progress',workflow:{column:'waiting',label:'Waiting for input'}}),
    node('contradiction','Issue',{todo_progress:'done',workflow:{column:'review'}}),
    node('unknown','Project',{workflow:{column:'review'}}),
  ]));
  assert.equal(report.reviewing,1);
  assert.equal(report.attentionTotal,2);
  assert.equal(report.started,2);
  assert.equal(report.unclassified,1);
});

test('large snapshots keep full scoped counts with bounded project and attention lists and stable order', () => {
  const nodes = Array.from({length:1500},(_,i)=>node(String(i).padStart(4,'0'),'Project',{todo_progress:'in_progress',workflow:{column:'waiting'}}));
  const report = briefing(snapshot([...nodes].reverse()));
  assert.equal(report.projects.length,6); assert.equal(report.projectTotal,1500);
  assert.equal(report.attention.length,4); assert.equal(report.attentionTotal,1500);
  assert.deepEqual(report.projects.map(n=>n.id),['0000','0001','0002','0003','0004','0005']);
});

test('refresh keeps briefing reader triggers and scroll, while a credential reset removes private content', () => {
  const document = treeDocument(), elements = new Map();
  document.getElementById = id => {
    if (!elements.has(id)) elements.set(id,document.createElement('div'));
    return elements.get(id);
  };
  const c = contextWithFunctions(['renderManagerOverview','reconcileChildren'],{document,
    DevgraphCheckIn:{briefing}, text(id,value){document.getElementById(id).textContent=String(value);},
    make(tag,cls,value){const el=document.createElement(tag);if(cls)el.className=cls;if(value!==undefined)el.textContent=value;return el;},
  });
  const data = snapshot([node('private')]);
  c.renderManagerOverview(data);
  const list=document.getElementById('manager-projects'), button=list.querySelector('button');
  document.activeElement=button;list.scrollTop=42;
  c.renderManagerOverview(data);
  assert.equal(list.querySelector('button'),button); assert.equal(document.activeElement,button);assert.equal(list.scrollTop,42);
  c.renderManagerOverview(null);
  assert.doesNotMatch(list.textContent,/private/);
  assert.equal(document.getElementById('manager-started').textContent,'—');
});
