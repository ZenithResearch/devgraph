import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initialize, connectCastalia} from '../dist/index.js';

const wasm = new Uint8Array(await readFile(new URL('../dist/internal/devgraph_web_bg.wasm', import.meta.url)));
const identity = {actor_id:'actor-original',receiver_profile:'302680d9f3a263a4897bc855abcebc6fa9abbca00d08c69af76757da88b07e5a',stable_issuer:'secs:test-work',audience:'devgraph://receiver-local',origin:'http://127.0.0.1:8080',capabilities:['read','work.v1']};
const request = {schema:'devgraph.work-request.v1',operation:'create',kind:'Issue',id:'i-1',expected_version:null,payload:{id:'i-1',title:'Example'}};
const key = 'sdk-lifecycle-key-123456';
const work = {id:'i-1',kind:'Issue',title:'Example',description:'',status:'draft',version:1,priority:0,artifact_ids:[],external_link_ids:[]};
const receipt = {receipt_id:'receipt-1',operation:'devgraph.work.create.v1',subject_label:'Issue',subject_id:'i-1',receipt_status:'pending',duplicate:false,correlation_id:'dg:sha256:'+'1'.repeat(64)};
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; }
async function until(predicate) { for(let n=0;n<100&&!predicate();n++) await tick(); assert.ok(predicate(),'fixture barrier reached'); }

async function fixture() {
  const state = {calls:[],connects:0,serial:0,identity:{...identity},hook:null,streams:new Map()};
  state.header = (value,status=200,dispatched=false) => {
    const stream_id = `stream-${++state.serial}`, raw=Buffer.from(typeof value==='string'?value:JSON.stringify(value));
    state.streams.set(stream_id,raw);
    return {stream_id,status,content_type:status>=400?'application/problem+json':'application/json',content_encoding:'identity',content_length:String(raw.length),limit:status>=400?65536:8388608,dispatched};
  };
  state.invoke = async message => {
    state.calls.push(message);
    if(message.action==='connect') state.connects++;
    if(state.hook) { const result=await state.hook(message); if(result!==undefined) return result; }
    if(message.action==='connect') return {...state.identity,connection_id:`connection-${state.connects}`};
    if(message.action==='dispose') return {disposed:true};
    if(message.action==='cancel') return {cancelled:true,dispatched:false};
    if(message.action==='request_read_access') return {read_context:`read-${++state.serial}`,expires_at:Math.floor(Date.now()/1000)+900};
    if(message.action==='authorize') return {authorization_id:`authorization-${++state.serial}`,expires_at:Math.floor(Date.now()/1000)+60};
    if(message.action==='execute') return state.header({work,receipt},201,true);
    if(message.action==='read') return state.header(work);
    if(message.action==='pull') {const raw=state.streams.get(message.stream_id);return {stream_id:message.stream_id,seq:0,total:raw.length,chunk_b64:raw.toString('base64url'),done:true};}
    throw Error('unexpected fixture action');
  };
  globalThis.castaliaWallet={devgraph:state.invoke};
  const runtime=await initialize({wasm}), connection=await connectCastalia(), client=runtime.createClient({connection});
  return {...state,state,runtime,connection,client,close:async()=>{runtime.dispose();await connection.dispose();delete globalThis.castaliaWallet;}};
}

test('failed reconnect is retryable with the same prepared request and invalidates old grants',async()=>{
  const f=await fixture();try {
    const prepared=f.client.prepare(request,{idempotency_key:key}), oldAttempt=await prepared.authorize();
    const read_context=await f.connection.requestReadAccess();
    f.state.hook=message=>{if(message.action==='connect'&&f.state.connects===2)throw {code:'transport_unavailable',dispatched:false};};
    await assert.rejects(f.connection.reconnect(),{code:'transport_unavailable'});
    await assert.rejects(prepared.authorize(),{code:'connection_lost'});
    assert.equal((await oldAttempt.execute()).kind,'not_dispatched');
    await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'read_context_expired'});
    await f.connection.reconnect();
    await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'read_context_expired'});
    assert.equal((await(await prepared.authorize()).execute()).kind,'committed');
    const authorizations=f.state.calls.filter(message=>message.action==='authorize');
    assert.equal(authorizations.length,2);
    assert.equal(authorizations[0].request_b64,authorizations[1].request_b64);
    assert.equal(authorizations[0].idempotency_key,authorizations[1].idempotency_key);
    assert.notEqual(authorizations[0].connection_id,authorizations[1].connection_id);
  }finally{await f.close();}
});

test('a successful reconnect invalidates unused authorization and read handles',async()=>{
  const f=await fixture();try {
    const prepared=f.client.prepare(request,{idempotency_key:key}), attempt=await prepared.authorize();
    const read_context=await f.connection.requestReadAccess();
    await f.connection.reconnect();
    const result=await attempt.execute();
    assert.equal(result.kind,'not_dispatched');assert.equal(result.error.dispatched,false);
    assert.equal(f.state.calls.filter(message=>message.action==='execute').length,0);
    await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'read_context_expired'});
    await f.connection.requestReadAccess();
  }finally{await f.close();}
});

test('reconnect retains the original identity anchor after a mismatch',async()=>{
  const f=await fixture();try {
    f.state.identity.actor_id='other-actor';
    await assert.rejects(f.connection.reconnect(),{code:'connection_binding_changed'});
    assert.equal(f.connection.profile.actor_id,'actor-original');
    assert.ok(f.state.calls.some(message=>message.action==='dispose'&&message.connection_id==='connection-2'));
    f.state.identity.actor_id='actor-original';
    await f.connection.reconnect();
    assert.equal(f.connection.profile.actor_id,'actor-original');
    await f.connection.requestReadAccess();
  }finally{await f.close();}
});

test('concurrent reconnects coalesce and transport actions fail immediately while reconnecting',async()=>{
  const f=await fixture(), cleanup=deferred();try {
    f.state.hook=message=>message.action==='dispose'?cleanup.promise:undefined;
    const first=f.connection.reconnect(), second=f.connection.reconnect();
    await assert.rejects(f.connection.requestReadAccess(),{code:'connection_lost'});
    const prepared=f.client.prepare(request,{idempotency_key:key});
    await assert.rejects(prepared.authorize(),{code:'connection_lost'});
    cleanup.resolve({disposed:true});
    await Promise.all([first,second]);
    assert.equal(f.state.connects,2);
    assert.equal(f.state.calls.filter(message=>message.action==='dispose').length,1);
  }finally{cleanup.resolve({disposed:true});f.state.hook=null;await f.close();}
});

test('dispose during old cleanup settles reconnect and prevents its handshake',async()=>{
  const f=await fixture(), cleanup=deferred();try {
    f.state.hook=message=>message.action==='dispose'?cleanup.promise:undefined;
    const pending=f.connection.reconnect(), rejected=assert.rejects(pending,{code:'disposed'});
    await until(()=>f.state.calls.some(message=>message.action==='dispose'));
    const disposed=f.connection.dispose();
    await rejected;
    cleanup.resolve({disposed:true});await disposed;await tick();
    assert.equal(f.state.connects,1);
    await assert.rejects(f.connection.reconnect(),{code:'disposed'});
  }finally{cleanup.resolve({disposed:true});f.state.hook=null;await f.close();}
});

test('dispose settles pending reconnect and disposes its late candidate exactly once',async()=>{
  const f=await fixture(), handshake=deferred();try {
    f.state.hook=message=>message.action==='connect'?handshake.promise:undefined;
    const pending=f.connection.reconnect(), rejected=assert.rejects(pending,{code:'disposed'});
    await until(()=>f.state.connects===2);
    await f.connection.dispose();await rejected;
    handshake.resolve({...identity,connection_id:'late-connection'});await tick();await tick();
    assert.equal(f.connection.profile.connection_id,'connection-1');
    assert.equal(f.state.calls.filter(message=>message.action==='dispose'&&message.connection_id==='late-connection').length,1);
    await f.connection.dispose();
  }finally{handshake.resolve({...identity,connection_id:'late-connection'});f.state.hook=null;await f.close();}
});

test('a timed-out handshake cannot replace a newer connection and uses its own cleanup ID',async t=>{
  const f=await fixture(), oldHandshake=deferred();
  const timers=[];
  t.mock.method(globalThis,'setTimeout',(callback,delay)=>{const timer={callback,delay,cleared:false};timers.push(timer);return timer;});
  t.mock.method(globalThis,'clearTimeout',timer=>{if(timer)timer.cleared=true;});
  try {
    f.state.hook=message=>message.action==='connect'&&f.state.connects===2?oldHandshake.promise:undefined;
    const first=f.connection.reconnect(), rejected=assert.rejects(first,{code:'timeout'});
    await until(()=>f.state.connects===2);
    timers.find(timer=>timer.delay===10000&&!timer.cleared).callback();await rejected;
    await f.connection.reconnect();
    assert.equal(f.connection.profile.connection_id,'connection-3');
    oldHandshake.resolve({...identity,connection_id:'late-connection-2'});await tick();await tick();
    assert.equal(f.connection.profile.connection_id,'connection-3');
    assert.ok(f.state.calls.some(message=>message.action==='dispose'&&message.connection_id==='late-connection-2'));
    assert.ok(!f.state.calls.some(message=>message.action==='dispose'&&message.connection_id==='connection-3'));
  }finally{oldHandshake.resolve({...identity,connection_id:'late-connection-2'});f.state.hook=null;await f.close();}
});

test('late authorization cancellation retains its originating connection ID',async()=>{
  const f=await fixture(), authorization=deferred();try {
    const prepared=f.client.prepare(request,{idempotency_key:key});
    f.state.hook=message=>message.action==='authorize'?authorization.promise:undefined;
    const pending=prepared.authorize(), rejected=assert.rejects(pending);
    await until(()=>f.state.calls.some(message=>message.action==='authorize'));
    await f.connection.reconnect();await rejected;
    authorization.resolve({authorization_id:'late-authorization',expires_at:Math.floor(Date.now()/1000)+60});await tick();await tick();
    const cleanup=f.state.calls.find(message=>message.action==='cancel'&&message.target_id==='late-authorization');
    assert.equal(cleanup?.connection_id,'connection-1');
  }finally{authorization.resolve({authorization_id:'late-authorization',expires_at:Math.floor(Date.now()/1000)+60});f.state.hook=null;await f.close();}
});

test('mutation dispatch diagnostics describe the mutation rather than a later pull RPC',async()=>{
  const f=await fixture();try {
    const prepared=f.client.prepare(request,{idempotency_key:key});
    for(const [name,reply,expectedKind,expectedDispatch] of [
      ['trusted denial',()=>{throw {code:'authorization_expired',dispatched:false};},'not_dispatched',false],
      ['no report',()=>{throw Error('private transport reason');},'outcome_unknown',null],
      ['dispatch risk',()=>{throw {code:'connection_lost',dispatched:true};},'outcome_unknown',true],
      ['HTTP rejection',()=>f.state.header({status:403,title:'Named Work authority denied',detail:''},403,true),'rejected',true],
      ['HTTP audit failure',()=>f.state.header({status:503,title:'Audit unavailable',detail:''},503,true),'outcome_unknown',true],
      ['malformed stream',()=>({...f.state.header({work,receipt},201,true),dispatched:false}),'outcome_unknown',null],
      ['bad response',()=>f.state.header('{',201,true),'outcome_unknown',true],
    ]) {
      f.state.hook=message=>message.action==='execute'?reply():undefined;
      const result=await(await prepared.authorize()).execute();
      assert.equal(result.kind,expectedKind,name);assert.equal(result.error.dispatched,expectedDispatch,name);
    }
    f.state.hook=null;
    const aborted=new AbortController();aborted.abort();
    const before=await(await prepared.authorize()).execute({signal:aborted.signal});
    assert.equal(before.kind,'not_dispatched');assert.equal(before.error.dispatched,false);
    const pull=deferred(), controller=new AbortController();
    const attempt=await prepared.authorize();let pulling=false;
    f.state.hook=message=>{if(message.action==='pull'){pulling=true;return pull.promise;}};
    const pending=attempt.execute({signal:controller.signal});await until(()=>pulling);controller.abort();
    const result=await pending;assert.equal(result.kind,'outcome_unknown');assert.equal(result.error.dispatched,true);
    pull.resolve({});
  }finally{f.state.hook=null;await f.close();}
});
