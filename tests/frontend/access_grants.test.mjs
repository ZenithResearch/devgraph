import{test}from'node:test';import assert from'node:assert/strict';import{makeSelection}from'../../src/devgraph/frontend/static/access/core.mjs';import{AccessOperatorClient}from'../../src/devgraph/frontend/static/access/client.mjs';import{AccessRequestClient}from'../../src/devgraph/frontend/static/credential-v2/access-request.mjs';
const ceiling={schema:'devgraph.access-draft.v2',issuer:'fixture',receiver:'https://example.test',write_audience:'devgraph://receiver-local',not_before:99,not_after:1000,lifetime_seconds:300,read_grants:[{scope:'devgraph.work.read',operations:['get','list'],relationship_types:['parent'],work_kinds:['Todo','Task'],include_archived:false,work_ids:['Task/one','Task/two']}],write_grants:[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task',ids:['one','two']}]},{operation:'devgraph.work.progress.set.v2',resources:[{kind:'Todo'}]}]};
const identity={subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32)};
const store=()=>{const m=new Map();return{getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}};
const json=data=>({ok:true,json:async()=>data});
test('selection narrows operations and per-operation resources without borrowing a cross product',()=>{
 const result=makeSelection({ceiling,ceilingDigest:'d'.repeat(64),recipient:identity,mode:'both',operations:['devgraph.work.patch.v2'],workIds:['Task/one'],expiresAt:500,now:100});
 assert.deepEqual(result.write.write_grants,[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task',ids:['one']}]}]);
 assert.deepEqual(result.read.read_grants[0].work_ids,['Task/one']);assert.equal(result.write.installation_id,undefined);assert.equal(result.read.installation_id,identity.installation_id);
 assert.throws(()=>makeSelection({ceiling,ceilingDigest:'d'.repeat(64),recipient:identity,mode:'both',arenaIds:['zenith'],expiresAt:500,now:100}),/arena_scoped_writes_unsupported/);
 assert.throws(()=>makeSelection({ceiling,recipient:identity,mode:'read',workIds:['Task/private'],expiresAt:500,now:100}),/read_scope_unavailable/);
});
test('unknown operator dispatch survives reload and recovers status without another decision',async()=>{
 const storage=store(),calls=[];let lose=true,originalResult;
 const wallet={requestIdentity:identity,approveAccess:async challenge=>({challenge,signature:'x'.repeat(86),proof:{private:'must not persist'}})};
 const fetchImpl=async(path,options)=>{const body=JSON.parse(options.body);calls.push({path,body});if(path.endsWith('challenges'))return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action:body.action}});const a=body.challenge.details.action;if(a.action==='inbox'){originalResult={schema:'devgraph.access-operator-result.v1',action_id:a.action_id,inbox:{total:0,items:[]},ceiling,ceiling_digest_sha256:'d'.repeat(64),authority_issued:false};if(lose)throw Error('lost');return json(originalResult);}assert.equal(a.action,'status');return json({schema:'devgraph.access-operator-result.v1',action_id:a.action_id,original_action_id:a.original_action_id,status:'reconciled',result:originalResult,authority_issued:false});};
 let c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare({action:'inbox'});await assert.rejects(c.approve(),/operator_outcome_unknown/);assert.equal(c.state,'unknown');assert.ok(!storage.getItem(c.key).includes('private'));
 c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await assert.rejects(c.prepare({action:'inbox'}),/recover_original_action_first/);await c.prepareRecovery();assert.equal(JSON.parse(storage.getItem(c.key)).action.action,'inbox');
 // Reload while status recovery itself is waiting: original action remains retained.
 c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepareRecovery();const result=await c.approve();assert.deepEqual(result,originalResult);assert.equal(storage.getItem(c.key),null);assert.equal(calls.filter(c=>c.path.endsWith('/actions')&&c.body.challenge.details.action.action==='inbox').length,1);
});
test('partial operator result remains unknown and retains original recovery data',async()=>{
 const storage=store(),wallet={requestIdentity:identity,approveAccess:async challenge=>({challenge,signature:'x'.repeat(86)})};let action;
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);if(path.endsWith('challenges')){action=body.action;return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action}});}return json({schema:'devgraph.access-operator-result.v1',action_id:action.action_id,authority_issued:false});};
 const c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare({action:'inbox'});await assert.rejects(c.approve(),/operator_outcome_unknown/);assert.ok(storage.getItem(c.key));assert.equal(c.state,'unknown');
});
test('requester retains the original ID and performs status-only recovery after lost response',async()=>{
 const storage=store(),calls=[];const wallet={requestIdentity:identity,approveAccess:async challenge=>({challenge,signature:'x'.repeat(86),proof:{}})};
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);calls.push(body);if(path.endsWith('challenges'))return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:body.request});throw Error('lost');};
 let c=new AccessRequestClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare();const original=c.original.request_id;await assert.rejects(c.approve(),/access_request_outcome_unknown/);c=new AccessRequestClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare();assert.equal(c.prepared.details.action,'status');assert.equal(c.prepared.details.request_id,original);assert.equal(calls.filter(x=>x.challenge?.details?.action==='submit').length,1);
});

test('unsigned operator preparation can restart after reload without inventing an unknown dispatch',async()=>{
 const storage=store(),wallet={requestIdentity:identity,approveAccess:()=>{throw Error('must not sign')}};
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action:body.action}});};
 let c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare({action:'inbox'});c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});assert.equal(c.state,'idle');await c.prepare({action:'inbox'});assert.equal(c.state,'prepared');
});


test('operator challenge cannot change the proposed action before consent',async()=>{
 const storage=store(),wallet={requestIdentity:identity,approveAccess:()=>{throw Error('must not sign')}};
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action:{...body.action,action:'deny',reason:'injected'}}});};
 const c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await assert.rejects(c.prepare({action:'inbox'}),/invalid_operator_challenge/);assert.equal(storage.getItem(c.key),null);
});

test('operator review result must match the exact requested selection and digest',async()=>{
 const storage=store(),wallet={requestIdentity:identity,approveAccess:async challenge=>({challenge,signature:'x'.repeat(86)})};
 const selection={read:structuredClone(ceiling)};delete selection.read.write_grants;let action;
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);if(path.endsWith('challenges')){action=body.action;return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action}});}return json({schema:'devgraph.access-operator-result.v1',action_id:action.action_id,authority_issued:false,request_id:action.request_id,expected_version:1,request_digest_sha256:action.request_digest_sha256,review_id:action.action_id,selection:{...action.selection,injected:true},selection_digest_sha256:'d'.repeat(64),plans:{read:{}}});};
 const c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare({action:'review',request_id:'c'.repeat(32),expected_version:1,request_digest_sha256:'d'.repeat(64),selection});await assert.rejects(c.approve(),/operator_outcome_unknown/);assert.equal(c.state,'unknown');assert.ok(storage.getItem(c.key));
});

for(const tamper of [false,true])test('read activation result binds the exact assignment: tamper='+tamper,async()=>{
 const storage=store(),wallet={requestIdentity:identity,approveAccess:async challenge=>({challenge,signature:'x'.repeat(86)})};let action,c;
 const selected=makeSelection({ceiling,ceilingDigest:'d'.repeat(64),recipient:identity,mode:'read',workIds:['Task/one'],expiresAt:500,now:100});
 const fetchImpl=async(path,o)=>{const body=JSON.parse(o.body);if(path.endsWith('challenges')){action=body.action;return json({schema:'castalia.access-request-challenge.v1',receiver:'https://example.test',...identity,details:{action}});}return json({schema:'devgraph.access-operator-result.v2',action_id:action.action_id,authority_issued:false,request_id:action.request_id,request_digest_sha256:action.request_digest_sha256,version:2,status:'active',activation:{active:true,credential_issued:false,assignment_digest:tamper?'f'.repeat(64):c.pending.assignment_digest}});};
 c=new AccessOperatorClient({wallet,storage,origin:'https://example.test',fetchImpl});await c.prepare({schema:'devgraph.access-operator-action.v2',action:'approve',request_id:'c'.repeat(32),request_digest_sha256:'d'.repeat(64),expected_version:1,previous_assignment_digest:null,review_id:'e'.repeat(32),selection:selected});
 if(tamper){await assert.rejects(c.approve(),/operator_outcome_unknown/);assert.ok(storage.getItem(c.key));}
 else{assert.equal((await c.approve()).status,'active');assert.equal(storage.getItem(c.key),null);}
});
