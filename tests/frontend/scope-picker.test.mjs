import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {validateCatalog,validPath,preferenceFor,normalizePaths,pathCovers,scopeTree,pathLabel} from '../../src/devgraph/frontend/static/credential-v2/scope-picker.mjs';
import {makeSelection,narrowPaths} from '../../src/devgraph/frontend/static/access/core.mjs';
const fixture=JSON.parse(await readFile(new URL('../../docs/contracts/access-catalog/v1/catalog.example.json',import.meta.url)));
const path=(...path)=>({schema:'devgraph.resource-path.v1',path});
test('server catalog requests names and paths without inventing authority',()=>{
 const catalog=validateCatalog(fixture,'https://example.test'),p=catalog.scopes.find(s=>s.resource.startsWith('Project/')).resource_path;
 assert.equal(preferenceFor(catalog,'contributor',[p]).id,'contributor');assert.deepEqual(preferenceFor(catalog,'',[]),{catalog_digest_sha256:catalog.catalog_digest_sha256,id:null,version:null});
 assert.throws(()=>preferenceFor(catalog,'admin'));assert.throws(()=>preferenceFor(catalog,'',[path('Arena/private')]));assert.throws(()=>preferenceFor(catalog,'',[p,p]));
 assert.throws(()=>validateCatalog({...catalog,receiver:'https://wrong.test'},'https://example.test'));
 assert.equal(validPath(path('Project/p','Arena/a')),false);assert.equal(validPath({...p,hidden:'yes'}),false);
});
test('path intersections retain every condition and disjoint means deny',()=>{
 assert.deepEqual(narrowPaths([path('Arena/a','Project/p')],[path('Initiative/i','Project/p')]),[path('Arena/a','Initiative/i','Project/p')]);
 assert.deepEqual(narrowPaths([path('Arena/a')],[path('Arena/b')]),[]);
 const ceiling={schema:'devgraph.access-draft.v4',not_before:1,not_after:100,read_grants:[{operations:['get'],work_kinds:['Task'],resource_paths:[path('Arena/a')]}],write_grants:[]};
 assert.throws(()=>makeSelection({ceiling,ceilingDigest:'a'.repeat(64),recipient:{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)},resourcePaths:[path('Arena/b')],expiresAt:90,now:10}),/read_scope_unavailable/);
});
test('path selection cannot imply unattached creation or provenance access',()=>{
 const ceiling={schema:'devgraph.access-draft.v4',not_before:1,not_after:100,read_grants:[],write_grants:[{operation:'devgraph.work.create.v2',resources:[{kind:'Task'}]},{operation:'devgraph.work.patch.v2',resources:[{kind:'Task'},{kind:'ReviewPacket'}]}]};
 const s=makeSelection({ceiling,ceilingDigest:'a'.repeat(64),recipient:{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)},resourcePaths:[path('Project/p')],mode:'write',operations:ceiling.write_grants.map(g=>g.operation),expiresAt:90,now:10});
 assert.equal(s.write.write_grants.length,1);assert.deepEqual(s.write.write_grants[0].resources,[{kind:'Task'}]);assert.deepEqual(s.write.write_grants[0].resource_paths,[path('Project/p')]);
});

test('preset request persists before consent, recovers exact bytes, and can correct unsigned rejection',async()=>{
 const {AccessRequestClient}=await import('../../src/devgraph/frontend/static/credential-v2/access-request.mjs');
 const values=new Map(),storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 const identity={subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32)};let reject=true,original,submits=0;
 const wallet={requestIdentity:identity,approveAccess:async challenge=>{assert.ok(values.size>0,'original must persist before consent');return {challenge,signature:'synthetic'};}};
 const fetchImpl=async(url,init)=>{const body=JSON.parse(init.body);
  if(url.endsWith('/challenges')){if(reject)return {ok:false,json:async()=>({error:'access_catalog_changed'})};return {ok:true,json:async()=>({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',details:body.request})};}
  if(body.challenge.details.action==='submit'){submits++;original=body.challenge.details;throw Error('lost response');}
  assert.equal(body.challenge.details.request_id,original.request_id);return {ok:true,json:async()=>({schema:'devgraph.access-request-result.v4',request_id:original.request_id,authority_issued:false,status:'partially_active',grant_active:false,read_active:true,write_active:false})};
 };
 let c=new AccessRequestClient({wallet,storage,fetchImpl,origin:'https://example.test'});
 const path=fixture.scopes.at(-1).resource_path;
 await assert.rejects(c.prepare({catalog:fixture,presetID:'contributor',resourcePaths:[path]}),/access_catalog_changed/);assert.equal(c.original,null);assert.equal(values.size,0);
 reject=false;await c.prepare({catalog:fixture,presetID:'project-manager',resourcePaths:[path]});const before=structuredClone(c.original);await assert.rejects(c.approve(),/outcome_unknown/);
 c=new AccessRequestClient({wallet,storage,fetchImpl,origin:'https://example.test'});await c.prepare();assert.equal(c.action.action,'status');assert.equal(c.action.intent,null);assert.deepEqual(c.original,before);await c.approve();assert.equal(submits,1);c.startNew();assert.equal(c.original,null);assert.ok([...values.values()].some(v=>JSON.parse(v).request_id===before.request_id));
});


test('multi-area union keeps distinct arenas and initiatives; selecting a parent subsumes only its descendants',()=>{
 const a=path('Arena/a'),ap=path('Arena/a','Project/p'),b=path('Arena/b'),bi=path('Arena/b','Initiative/i');
 assert.deepEqual(normalizePaths([ap,b,a]),[b,a]);
 assert.deepEqual(normalizePaths([ap,bi]),[ap,bi]);
 assert.equal(pathCovers(a,ap),true);assert.equal(pathCovers(a,bi),false);
 assert.throws(()=>normalizePaths(Array.from({length:17},(_,i)=>path('Project/p'+i))),/too_many_resource_paths/);
});
test('tree shows only disclosed ancestry, uses readable titles and preserves typed IDs',()=>{
 const tree=scopeTree(fixture.scopes);assert.equal(tree.length,1);assert.equal(tree[0].children[0].children[0].resource,'Project/project-a');
 assert.match(pathLabel(fixture.scopes.at(-1).resource_path,fixture.scopes),/Arena: A → Initiative: Fixture → Project: Fixture/);
 const published=fixture.scopes.at(-1),hidden=scopeTree([{...published,resource_path:path('Project/project-a'),parent:null}]);
 assert.equal(hidden.length,1);assert.equal(hidden[0].resource,'Project/project-a');assert.equal(hidden[0].children.length,0);
});
test('global selection adds no path filter and cannot remove the operator ceiling',()=>{
 const ceiling={schema:'devgraph.access-draft.v4',not_before:1,not_after:100,read_grants:[{operations:['get'],work_kinds:['Task'],resource_paths:[path('Arena/a')]}],write_grants:[]};
 const selected=makeSelection({ceiling,ceilingDigest:'a'.repeat(64),recipient:{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)},resourcePaths:[],expiresAt:90,now:10});
 assert.deepEqual(selected.read.read_grants[0].resource_paths,[path('Arena/a')]);
});
