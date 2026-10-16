import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createBindings} from '../dist/internal/devgraph_web_factory.js';
import {Wallet} from '../../../src/devgraph/frontend/static/kanban/wallet.mjs';
import {DevgraphCredentialClient} from '../../../src/devgraph/frontend/static/credential-v2/coordinator.mjs';
import {createDevgraphHttpTransport} from '../../../src/devgraph/frontend/static/credential-v2/transport.mjs';
const wasm = createBindings();
await wasm.default({module_or_path:new Uint8Array(await readFile(new URL('../dist/internal/devgraph_web_bg.wasm',import.meta.url)))});
const [vector]=JSON.parse(await readFile(new URL('../../../crates/devgraph-client-core/tests/fixtures/progress-results.json',import.meta.url)));
const raw=JSON.stringify(vector.request), key='kanban-original-request-001',origin='http://127.0.0.1:8080';
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':status<400?'application/json':'application/problem+json'}});
async function fixture(){
 const calls=[], state={mode:'valid',approvals:0};
 const provider={getCapabilities:async()=>['credential_presentation_v2','provider_profiles_v1','connection_v1'],
   getSubject:async()=>({publicKey:'ab'.repeat(32)}),proposeProviderProfile:async()=>({state:'approved'}),
   requestConnection:async()=>({state:'connected'}),presentCredential:async()=>{state.approvals++;return state.mode==='denied'?{state:'denied'}:{state:'approved',presentation:{schema:'castalia.credential-presentation.v2'}};}};
 const transport=createDevgraphHttpTransport({origin,fetch:async(url,options)=>{
   const action=url.split('/').at(-1);calls.push({action,body:JSON.parse(options.body)});
   if(action==='capabilities')return json({schema:'devgraph.credential-transport-capabilities.v2',operations:['devgraph.work.create.v2','devgraph.work.workflow.transition.v2']});
   if(action==='provider-profile')return json({schema:'castalia.provider-profile.v1',display_name:'Devgraph',origins:[origin],membership:null,presentations:[{issuer:'secs:test',key_id:'test',public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:origin}]}]});
   if(action==='prepare')return json({schema:'castalia.credential-presentation-request.v2',credential:{},disclosure:{}});
   if(state.mode==='lost')throw Error('lost');
   if(state.mode==='rejected'||state.mode==='status-rejected')return json({type:'about:blank',status:409,title:'Invalid status transition',detail:'Requirements not met'},409);
   if(state.mode==='proxy')return new Response('Bad Gateway',{status:502});
   if(state.mode==='unknown')return json({state:'outcome_unknown'});
   const result=action==='status'?{state:'committed',receipt:{...vector.result.receipt,duplicate:true}}:{state:'committed',...structuredClone(vector.result)};
   if(state.mode==='missing-schema')delete result.schema;
   if(state.mode==='missing-work')delete result.work;
   if(state.mode==='wrong-subject')result.receipt.subject_id='another';
   if(state.mode==='missing-correlation')delete result.receipt.correlation_id;
   if(state.mode==='bad-status')result.receipt.status='pending';
   if(state.mode==='bad-version')result.work.version=999;
   if(state.mode==='contradictory-duplicate')result.receipt.duplicate=true;
   if(state.mode==='duplicate-key')return new Response('{"state":"unknown",'+JSON.stringify(result).slice(1),{headers:{'content-type':'application/json'}});
   return json(result);
 }});
 const wallet=new Wallet(provider,async()=>({DevgraphCredentialClient,createDevgraphHttpTransport:()=>transport,wasm}),origin);
 await wallet.prepareSetup();await wallet.connect();return {wallet,calls,state};
}
test('Kanban accepts only complete canonical commits using the actual SDK WASM decoder',async()=>{
 const f=await fixture();try{
  const result=await f.wallet.execute(raw,key);assert.equal(result.state,'committed');
  assert.equal(result.work.progress,'not_started');
  const dispatch=f.calls.find(c=>c.action==='execute');assert.equal(dispatch.body.request,raw);assert.equal(dispatch.body.idempotency_key,key);
 }finally{f.wallet.dispose();}
});
test('Kanban preserves every malformed or lost outcome until exact status recovery',async()=>{
 for(const mode of ['missing-schema','missing-work','wrong-subject','missing-correlation','bad-status','bad-version','contradictory-duplicate','duplicate-key','proxy','unknown','lost']){
  const f=await fixture();try{
   f.state.mode=mode;await assert.rejects(f.wallet.execute(raw,key),{dispatched:true},mode);
   await assert.rejects(f.wallet.execute(raw,key+'-new'),{code:'reconciliation_required'},mode);
   f.state.mode='status-rejected';await assert.rejects(f.wallet.reconcile(raw,key),{dispatched:true});
   f.state.mode='valid';const result=await f.wallet.reconcile(raw,key);assert.equal(result.receipt.duplicate,true);assert.equal(result.work,null);
   assert.equal(f.calls.filter(c=>c.action==='execute').length,1);
   for(const call of f.calls.filter(c=>c.action==='status')){assert.equal(call.body.request,raw);assert.equal(call.body.idempotency_key,key);}
  }finally{f.wallet.dispose();}
 }
});
test('Kanban rejection and cancelled approval permit correction without false success',async()=>{
 const f=await fixture();try{
  f.state.mode='denied';await assert.rejects(f.wallet.execute(raw,key),{dispatched:false});assert.equal(f.calls.some(c=>c.action==='execute'),false);
  f.state.mode='rejected';await assert.rejects(f.wallet.execute(raw,key),{code:'request_rejected',dispatched:false,status:409});
  f.state.mode='valid';assert.equal((await f.wallet.execute(raw,key+'-corrected')).state,'committed');
 }finally{f.wallet.dispose();}
});
