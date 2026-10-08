import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initialize} from '../dist/index.js';
const wasm=new Uint8Array(await readFile(new URL('../dist/internal/devgraph_web_bg.wasm',import.meta.url)));
const [vector]=JSON.parse(await readFile(new URL('../../../crates/devgraph-client-core/tests/fixtures/progress-results.json',import.meta.url)));
const origin='http://127.0.0.1:8080',key='generic-sdk-request-0001';
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':status<400?'application/json':'application/problem+json'}});
async function fixture({provider=true}={}) {
  const state={connections:0,profiles:[],calls:[],mode:'normal',approvals:0,delay:null};
  const wallet={getSubject:async()=>({publicKey:'ab'.repeat(32)}),getCapabilities:async()=>['credential_presentation_v2','provider_profiles_v1','connection_v1'],requestConnection:()=>{state.connections++;return Promise.resolve({state:'connected'});},proposeProviderProfile:p=>{state.profiles.push(p);return Promise.resolve({state:'approved'});},presentCredential:async()=>{state.approvals++;if(state.delay)await state.delay;return state.mode==='denied'?{state:'denied'}:{state:'approved',presentation:{schema:'castalia.credential-presentation.v2'}};},devgraph:()=>{throw Error('old bridge must not be called');}};
  const request=async(url,options)=>{
    state.calls.push({url,options,body:options.body?JSON.parse(options.body):null});
    if(url.endsWith('/capabilities'))return json({operations:['devgraph.work.create.v2']});
    if(url.endsWith('/provider-profile'))return json({schema:'castalia.provider-profile.v1',display_name:'Devgraph',origins:[origin],membership:null,presentations:[{issuer:'secs:test',key_id:'test-key',public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:origin}]}]});
    if(url.endsWith('/prepare'))return json({schema:'castalia.credential-presentation-request.v2',credential:{claims:{}},disclosure:{title:'Devgraph request',statements:[]}});
    if(url.endsWith('/execute')){
      if(state.mode==='lost')throw Error('network lost');
      if(state.mode==='duplicate')return new Response('{"state":"unknown",'+JSON.stringify({state:'committed',...vector.result}).slice(1),{headers:{'content-type':'application/json'}});
      if(state.mode==='conflict')return json({type:'about:blank',status:412,title:'Version precondition failed'},412);
      const result=structuredClone(vector.result);if(state.mode==='invalid')result.work.progress='done';
      return json({state:'committed',...result});
    }
    if(url.endsWith('/status'))return json({state:'committed',receipt:{...vector.result.receipt,duplicate:true}});
    if(url.endsWith('/todos/v2/Todo/example'))return json(vector.result.work);
    if(url.includes('/work/Issue'))return json({items:[]});
    throw Error('unexpected request');
  };
  const runtime=await initialize({wasm});
  const client=runtime.connectDevgraph({origin,readCredential:'synthetic-read-only',provider:provider?wallet:undefined,fetch:request});
  return {state,client,runtime,prepare:()=>client.prepare(vector.request,{idempotency_key:key})};
}
test('generic SDK reads use explicit memory-only read credential without Wallet',async()=>{
  const f=await fixture({provider:false});try{
    const todo=await f.client.getTodo('Todo','example');assert.equal(todo.progress,'not_started');
    const call=f.state.calls[0];assert.equal(call.options.headers.Authorization,'Bearer synthetic-read-only');assert.equal(call.options.credentials,'omit');assert.equal(call.options.redirect,'error');
    const items=[];for await(const item of f.client.iterateWork({kind:'Issue'}))items.push(item);assert.deepEqual(items,[]);
    f.client.dispose();await assert.rejects(f.client.getTodo('Todo','example'),{code:'disposed'});assert.equal(f.state.calls.length,2);
  }finally{f.runtime.dispose();}
});
test('generic SDK uses actual WASM request and result validation with generic approval',async()=>{
  const f=await fixture();try{
    const prepared=f.prepare(),result=await prepared.execute();assert.equal(result.kind,'committed');assert.equal(result.work.progress,'not_started');assert.equal(f.state.approvals,1);
    for(const call of f.state.calls)assert.equal(call.options.headers.Authorization,undefined);
    assert.equal(f.state.calls.find(c=>c.url.endsWith('/execute')).body.request,new TextDecoder().decode(prepared.canonical_bytes));
  }finally{f.runtime.dispose();}
});
test('denial and cancellation during approval never dispatch',async()=>{
  for(const mode of ['denied','cancel']){
    const f=await fixture();try{
      f.state.mode=mode;let release;
      if(mode==='cancel')f.state.delay=new Promise(resolve=>release=resolve);
      const prepared=f.prepare(),pending=prepared.execute();
      if(mode==='cancel'){while(!f.state.approvals)await new Promise(resolve=>setTimeout(resolve,1));prepared.dispose();release();}
      assert.equal((await pending).kind,'not_dispatched');assert.equal(f.state.calls.some(c=>c.url.endsWith('/execute')),false);
    }finally{f.runtime.dispose();}
  }
});
test('unknown outcomes preserve request and require status-only recovery with new approval',async()=>{
  const f=await fixture();try{
    f.state.mode='lost';const prepared=f.prepare();assert.equal((await prepared.execute()).kind,'outcome_unknown');
    await assert.rejects(prepared.execute(),{code:'reconciliation_required'});
    f.state.mode='normal';const recovered=await prepared.reconcile();assert.equal(recovered.kind,'committed');assert.equal(recovered.receipt.duplicate,true);assert.equal(recovered.work,null);
    const execution=f.state.calls.find(c=>c.url.endsWith('/execute')),status=f.state.calls.find(c=>c.url.endsWith('/status'));
    assert.equal(status.body.request,execution.body.request);assert.equal(status.body.idempotency_key,execution.body.idempotency_key);assert.equal(f.state.approvals,2);
    assert.equal(f.state.calls.filter(c=>c.url.endsWith('/execute')).length,1);
  }finally{f.runtime.dispose();}
});
test('invalid canonical result stays unknown and a trusted version conflict is rejected',async()=>{
  for(const mode of ['invalid','duplicate','conflict']){
    const f=await fixture();try{f.state.mode=mode;assert.equal((await f.prepare().execute()).kind,mode==='conflict'?'rejected':'outcome_unknown');}
    finally{f.runtime.dispose();}
  }
});

test('packed SDK preloads provider setup and keeps trust and connection ceremonies separate',async()=>{
  const f=await fixture();try{
    const setup=await f.client.prepareWalletSetup();
    assert.equal(f.state.approvals,0);assert.equal(f.state.connections,0);assert.equal(f.state.profiles.length,0);
    const consent=setup.approve();assert.equal(f.state.profiles.length,1);await consent;
    assert.equal(f.state.connections,0);
    const connection=setup.connect();assert.equal(f.state.connections,1);await connection;
    assert.equal(f.state.approvals,0);
    f.client.dispose();assert.throws(()=>setup.approve(),{code:'disposed'});
  }finally{f.runtime.dispose();}
});
