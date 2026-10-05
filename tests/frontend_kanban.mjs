import test from 'node:test';
import assert from 'node:assert/strict';
import {filters,query,request,appendPage,safeUrl} from '../src/devgraph/frontend/static/kanban/core.mjs';
import {Wallet} from '../src/devgraph/frontend/static/kanban/wallet.mjs';
const raw=request({kind:'Task',id:'test-one',version:'7'},'workflow.transition',{stage:'intake',reason:''});
const key='stable-recovery-key-1234';
function provider({deny=false,lost=false,malformed=false}={}) {
 const calls=[];
 const body=JSON.stringify({work:{kind:'Task',id:'test-one'},receipt:{receipt_id:'receipt-1',operation:'devgraph.work.workflow.transition.v2',subject_label:'Task',subject_id:malformed?'other':'test-one',duplicate:false}});
 return {calls,async devgraph(m){calls.push(m);switch(m.action){
 case 'connect':return {connection_id:'c',actor_id:'actor',receiver_profile:'profile',capabilities:['read','work.v1','workflow.v1','progress.v1']};
 case 'authorize':if(deny)throw Object.assign(Error('Cancelled'),{dispatched:false});return {authorization_id:'a'};
 case 'execute':if(lost)throw Error('Port lost');return {stream_id:'s',status:200,content_type:'application/json',content_encoding:'identity',limit:8192,dispatched:true};
 case 'pull':return {stream_id:'s',seq:0,total:Buffer.byteLength(body),chunk_b64:Buffer.from(body).toString('base64url'),done:true};
 case 'dispose':return {disposed:true};
 default:throw Error('Unexpected action');
 }}};
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
 const p=provider(),wallet=new Wallet(p);await wallet.connect();
 const result=await wallet.execute(raw,key);assert.equal(result.receipt.receipt_id,'receipt-1');
 const authorized=p.calls.find(c=>c.action==='authorize');assert.equal(authorized.idempotency_key,key);assert.equal(Buffer.from(authorized.request_b64,'base64url').toString(),raw);
 assert.deepEqual(p.calls.map(c=>c.action),['connect','authorize','execute','pull']);
});
test('cancelled consent never dispatches',async()=>{
 const p=provider({deny:true}),wallet=new Wallet(p);await wallet.connect();
 await assert.rejects(wallet.execute(raw,key),e=>e.dispatched===false);
 assert.equal(p.calls.some(c=>c.action==='execute'),false);assert.equal(wallet.busy,false);
});
test('lost execution and unmatched receipt remain uncertain; no automatic retry',async()=>{
 for(const options of [{lost:true},{malformed:true}]){
 const p=provider(options),wallet=new Wallet(p);await wallet.connect();
 await assert.rejects(wallet.execute(raw,key),e=>e.dispatched===true);
 assert.equal(p.calls.filter(c=>c.action==='execute').length,1);
 }
});
test('old native bridge remains read-only and is disposed',async()=>{
 const calls=[];const wallet=new Wallet({devgraph:async m=>{calls.push(m.action);return {connection_id:'old',capabilities:['read','work.v1']}}});
 await assert.rejects(wallet.connect(),/needs the Todo progress update/);
 assert.equal(wallet.connection,null);assert.deepEqual(calls,['connect','dispose']);
});
