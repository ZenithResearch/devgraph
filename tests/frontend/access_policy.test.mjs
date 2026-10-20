import{test}from'node:test';import assert from'node:assert/strict';
import{makeSelection}from'../../src/devgraph/frontend/static/access/core.mjs';
import{policyView}from'../../src/devgraph/frontend/static/access/policy.mjs';
const recipient={subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32)};
const ceiling={schema:'devgraph.access-draft.v3',issuer:'issuer',receiver:'https://graph.example',not_before:1,not_after:1000,lifetime_seconds:300,read_grants:[{scope:'devgraph.work.read',operations:['list','get'],relationship_types:['parent'],work_kinds:['Todo','Task'],work_ids:['Todo/one','Task/two'],arena_ids:['research'],include_archived:false},{scope:'devgraph.work.read',operations:['get'],relationship_types:[],work_kinds:['Project'],arena_ids:['operations'],include_archived:true}],write_grants:[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task',ids:['two']},{kind:'Project'}]},{operation:'devgraph.work.progress.set.v2',resources:[{kind:'Todo',ids:['one']},{kind:'ReviewPacket',ids:['evidence']}]}]};
const selection=extra=>makeSelection({ceiling,recipient,ceilingDigest:'d'.repeat(64),mode:'read',expiresAt:900,now:10,...extra});
test('read operation and kind choices narrow each correlated grant',()=>{
 const result=selection({readOperations:['get'],workKinds:['Task']}).read;
 assert.equal(result.read_grants.length,1);assert.deepEqual(result.read_grants[0],{...ceiling.read_grants[0],operations:['get'],work_kinds:['Task'],work_ids:['Task/two']});assert.deepEqual(ceiling.read_grants[0].work_ids,['Todo/one','Task/two']);
 assert.throws(()=>selection({readOperations:['list'],workKinds:['Project']}),/read_scope_unavailable/);
 assert.throws(()=>selection({readOperations:['get'],workKinds:['Task'],arenaIds:['operations']}),/read_scope_unavailable/);
});
test('clearing all choices or requesting absent kinds/operations cannot widen a grant',()=>{
 for(const extra of [{readOperations:[]},{workKinds:[]},{readOperations:['relationships']},{workKinds:['Issue']}])assert.throws(()=>selection(extra),/scope_unavailable/);
 assert.throws(()=>selection({workKinds:['Invented']}),/invalid_kind_selection/);
 assert.throws(()=>selection({readOperations:['invented']}),/invalid_read_operation_selection/);
});
test('kind narrowing retains write operation-resource correlation',()=>{
 const result=selection({mode:'write',operations:['devgraph.work.patch.v2','devgraph.work.progress.set.v2'],workKinds:['Task']}).write;
 assert.deepEqual(result.write_grants,[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task',ids:['two']}]}]);
 assert.throws(()=>selection({mode:'write',operations:['devgraph.work.patch.v2'],workKinds:[]}),/write_scope_unavailable/);
});
test('policy display preserves independent rules and makes unsupported activation visible',()=>{
 const view=policyView({ceiling,policy_revision:7,policy_digest_sha256:'e'.repeat(64),activation:{read:true,write:false,mixed:false}});
 assert.equal(view.revision,7);assert.equal(view.groups.length,4);
 assert.deepEqual(view.groups[0].operations,['list','get']);assert.deepEqual(view.groups[0].arenas,['research']);assert.deepEqual(view.groups[0].objects,['Todo/one','Task/two']);
 assert.deepEqual(view.groups[1].operations,['get']);assert.deepEqual(view.groups[1].arenas,['operations']);assert.equal(view.groups[1].objects,null);
 assert.deepEqual(view.groups[3].resources,ceiling.write_grants[1].resources);
 assert.equal(view.writeAvailable,false);assert.equal(view.mixedAvailable,false);
 assert.deepEqual(view.kinds,['Project','ReviewPacket','Task','Todo']);
});

test('checkbox display order cannot alter the server canonical grant order',()=>{
 const read=selection({readOperations:['get','list'],workKinds:['Task','Todo']}).read;
 assert.deepEqual(read.read_grants[0].operations,['list','get']);assert.deepEqual(read.read_grants[0].work_kinds,['Todo','Task']);
});
