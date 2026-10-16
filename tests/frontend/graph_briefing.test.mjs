import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {contextWithFunctions, treeDocument} from './monitor_test_helpers.mjs';
const {briefing, createProjectFilters} = createRequire(import.meta.url)('../../src/devgraph/frontend/static/topology/check-in.js');
const node = (id, kind = 'Project', extra = {}) => ({key:`${kind}:${id}`, id, kind, title:id, category:'work', status:'draft', archived:false, ...extra});
const snapshot = (nodes, edges = []) => ({graph_nodes:nodes, graph_edges:edges});

test('briefing excludes archived work, deduplicates and never maps legacy lifecycle to progress or review', () => {
  const active = node('active', 'Task', {todo_progress:'in_progress', workflow:{column:'review',label:'Layer review'}});
  const report = briefing(snapshot([node('draft'), node('accepted','Initiative',{status:'accepted',progress:{percent:100}}),
    node('review','Project',{status:'review'}), node('archived','Project',{archived:true,todo_progress:'in_progress'}), active, active]));
  assert.equal(report.unclassified,3);
  assert.equal(report.started,1);
  assert.equal(report.reviewing,1);
  assert.equal(report.projectTotal,2);
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
    DevgraphCheckIn:{briefing}, projectFilters:createProjectFilters(null), text(id,value){document.getElementById(id).textContent=String(value);},
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

function projectFixture() {
  const nodes = [node('alpha','Arena'),node('beta','Arena'),node('one','Initiative'),node('two','Initiative'),
    node('a'),node('b'),node('standalone'),node('archived','Project',{archived:true}),node('task','Task')];
  const edge = (source,target,relationship='HAS_CHILD') => ({source,target,relationship});
  return snapshot(nodes,[edge('Arena:alpha','Initiative:one','CONTAINS_WORK'),edge('Arena:beta','Initiative:two','CONTAINS_WORK'),
    edge('Initiative:one','Project:a'),edge('Initiative:one','Project:archived'),edge('Initiative:two','Project:b'),
    edge('Project:a','Task:task'),edge('Project:standalone','Project:a','DEPENDS_ON')]);
}

test('project-only list includes standalone projects by default and intersects inherited Arena and Initiative filters', () => {
  const data=projectFixture(), all=briefing(data);
  assert.deepEqual(all.projects.map(n=>n.kind),['Project','Project','Project']);
  assert.equal(all.projectTotal,3);
  assert.deepEqual(all.projectArenas.map(n=>n.key),['Arena:alpha','Arena:beta']);
  const scoped=briefing(data,{arena:'Arena:alpha'});
  assert.deepEqual(scoped.projects.map(n=>n.id),['a']);
  assert.deepEqual(scoped.projectInitiatives.map(n=>n.key),['Initiative:one']);
  assert.deepEqual(briefing(data,{initiative:'Initiative:two'}).projects.map(n=>n.id),['b']);
  assert.equal(briefing(data,{arena:'Arena:alpha',initiative:'Initiative:two'}).projectTotal,0);
  assert.equal(briefing(data,{arena:'Initiative:one'}).projectTotal,0);
  assert.equal(briefing(data,{initiative:'Initiative:missing'}).projectTotal,0);
  assert.equal(scoped.attentionTotal,all.attentionTotal,'project selectors do not change the separate attention queue');
});

test('ancestor context preserves filters when parent nodes or hierarchy lines are hidden in the map', () => {
  const data=projectFixture();
  data.layout_context={nodes:data.graph_nodes.map(({key,kind,title})=>({key,kind,title})),edges:data.graph_edges.filter(e=>e.relationship!=='DEPENDS_ON')};
  data.graph_nodes=data.graph_nodes.filter(n=>n.kind==='Project');data.graph_edges=[];
  const report=briefing(data,{arena:'Arena:alpha',initiative:'Initiative:one'});
  assert.deepEqual(report.projects.map(n=>n.id),['a']);
  assert.equal(report.projectArenas.length,2);
  data.layout_context.edges.push({source:'Project:a',target:'Initiative:one',relationship:'HAS_CHILD'});
  assert.equal(briefing(data,{arena:'Arena:alpha'}).projectTotal,1,'malformed cycles remain bounded');
});

test('filtering precedes the six-row limit and keeps the entire matching count', () => {
  const data=projectFixture();
  for(let i=0;i<20;i++){
    const extra=node(`extra${i}`);data.graph_nodes.push(extra);
    data.graph_edges.push({source:'Initiative:one',target:extra.key,relationship:'HAS_CHILD'});
  }
  const report=briefing(data,{arena:'Arena:alpha',initiative:'Initiative:one'});
  assert.equal(report.projectTotal,21);assert.equal(report.projects.length,6);
  assert.deepEqual(briefing(data,{initiative:'Initiative:two'}).projects.map(n=>n.id),['b']);
});

test('saved project filters are scoped, allowlisted and clear on credential reset without storing authority', () => {
  const records=new Map(),storage={getItem:key=>records.get(key),setItem:(key,value)=>records.set(key,value)};
  const filters=createProjectFilters(storage);filters.setPreferenceKey('scope-a');
  filters.update({arena:'Arena:alpha',initiative:'Initiative:one',credential:'must-not-persist'});
  assert.deepEqual(JSON.parse(records.get('scope-a')),{version:1,arena:'Arena:alpha',initiative:'Initiative:one'});
  const reload=createProjectFilters(storage);reload.setPreferenceKey('scope-a');assert.deepEqual(reload.value,filters.value);
  reload.setPreferenceKey('scope-b');assert.deepEqual(reload.value,{arena:'',initiative:''});
  reload.setPreferenceKey('scope-a');reload.reset();assert.deepEqual(reload.value,{arena:'',initiative:''});
  assert.equal(records.size,1);
  for(const saved of ['broken',JSON.stringify({version:99,arena:'wrong'}),JSON.stringify({version:1,arena:[],initiative:{}})]){
    records.set('invalid',saved);const invalid=createProjectFilters(storage);invalid.setPreferenceKey('invalid');assert.deepEqual(invalid.value,{arena:'',initiative:''});
  }
  const denied=createProjectFilters({getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}});
  denied.setPreferenceKey('private');denied.update({arena:'Arena:alpha'});assert.equal(denied.value.arena,'Arena:alpha');
});
