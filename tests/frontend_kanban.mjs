import test from 'node:test';
import assert from 'node:assert/strict';
import {filters,query,request,appendPage,safeUrl} from '../src/devgraph/frontend/static/kanban/core.mjs';
import {Wallet} from '../src/devgraph/frontend/static/kanban/wallet.mjs';
const raw=request({kind:'Task',id:'test-one',version:'7'},'workflow.transition',{stage:'intake',reason:''});
const key='stable-recovery-key-1234';
function fixture({deny=false,lost=false,malformed=false}={}) {
 const calls=[];
 const result={state:'committed',work:{kind:'Task',id:'test-one'},receipt:{receipt_id:'receipt-1',operation:'devgraph.work.workflow.transition.v2',subject_label:'Task',subject_id:malformed?'other':'test-one',duplicate:false}};
 const provider={getCapabilities:async()=>['credential_presentation_v2','provider_profiles_v1','connection_v1'],requestConnection:async()=>({state:'connected'}),proposeProviderProfile:async()=>({state:'approved'}),presentCredential:async()=>{},getSubject:async()=>({publicKey:'ab'.repeat(32)})};
 class Client {
  async execute(request,idempotency,options){calls.push({request,idempotency,options});if(deny)throw Object.assign(Error('Denied'),{dispatched:false});if(lost)throw Object.assign(Error('Lost'),{dispatched:true});return result;}
  dispose(){}
 }
 const modules=async()=>({DevgraphCredentialClient:Client,createDevgraphHttpTransport:()=>({getProviderProfile:async()=>({schema:'castalia.provider-profile.v1',display_name:'Devgraph',membership:null,origins:['http://127.0.0.1:8080'],presentations:[{issuer:'secs:test',key_id:'test',public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:'http://127.0.0.1:8080'}]}]}),getCapabilities:async()=>({schema:'devgraph.credential-transport-capabilities.v2',operations:['devgraph.work.workflow.transition.v2']})})});
 return {calls,wallet:new Wallet(provider,modules,'http://127.0.0.1:8080')};
}
test('only view filters persist and exact versions remain lossless',()=>{
 const f=filters({scope:'Task/no',kind:'Artifact',credential:'never-save',authorization:'never-save',q:'x'.repeat(500)});
 assert.equal(f.scope,'');assert.equal(f.kind,'');assert.equal(f.q.length,200);assert.equal('credential' in f,false);
 assert.equal(query({scope:'Initiative/example',descendants:true,column:'review'}),'scope=Initiative%2Fexample&descendants=true&column=review&archived=exclude');
 assert.equal(JSON.parse(raw).expected_version,7);
 assert.throws(()=>request({kind:'Task',id:'x',version:'9007199254740992'},'workflow.transition',{}));
 assert.equal(safeUrl('javascript:alert(1)'),null);assert.equal(safeUrl('https://user@example.test'),null);
 assert.deepEqual(appendPage([{key:'a'}],[{key:'a'},{key:'b'}]),[{key:'a'},{key:'b'}]);
});
test('success waits for matching committed receipt and preserves exact request/key',async()=>{
 const {wallet,calls}=fixture();await wallet.prepareSetup();await wallet.connect();
 const result=await wallet.execute(raw,key);assert.equal(result.receipt.receipt_id,'receipt-1');
 assert.equal(calls[0].request,raw);assert.equal(calls[0].idempotency,key);assert.equal(calls.length,1);
});
test('generic approval denial remains definitely undispatched',async()=>{
 const {wallet}=fixture({deny:true});await wallet.prepareSetup();await wallet.connect();
 await assert.rejects(wallet.execute(raw,key),e=>e.dispatched===false);assert.equal(wallet.busy,false);
});
test('lost execution and unmatched receipt remain uncertain; no automatic retry',async()=>{
 for(const options of [{lost:true},{malformed:true}]){
 const {wallet,calls}=fixture(options);await wallet.prepareSetup();await wallet.connect();
 await assert.rejects(wallet.execute(raw,key),e=>e.dispatched===true);assert.equal(calls.length,1);
 }
});
test('explicit recovery checks status and never resubmits a mutation',async()=>{
 const {wallet,calls}=fixture();await wallet.prepareSetup();await wallet.connect();await wallet.reconcile(raw,key);
 assert.deepEqual(calls[0].options,{reconcile:true});
});
test('legacy native bridge is not admitted as generic approval support',async()=>{
 const calls=[];const wallet=new Wallet({devgraph:async m=>{calls.push(m.action);}});
 await assert.rejects(wallet.prepareSetup(),/credential-bound approvals/);
 assert.equal(wallet.connection,null);assert.deepEqual(calls,[]);
});
