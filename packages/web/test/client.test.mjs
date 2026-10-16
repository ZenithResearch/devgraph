import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initialize,connectCastalia,CastaliaConnection,DevgraphError} from '../dist/index.js';

const bytes = new Uint8Array(await readFile(new URL('../dist/internal/devgraph_web_bg.wasm',import.meta.url)));
const vectors = [...JSON.parse(await readFile(new URL('../../../tests/fixtures/sdk-work-v1/requests.json',import.meta.url))), ...JSON.parse(await readFile(new URL('../../../tests/fixtures/shared-work-protocol/workflow-v1/requests.json',import.meta.url))), ...JSON.parse(await readFile(new URL('../../../crates/devgraph-client-core/tests/fixtures/progress-requests.json',import.meta.url)))];
const request = {schema:'devgraph.work-request.v1',operation:'create',kind:'Issue',id:'i-1',expected_version:null,payload:{id:'i-1',title:'Example'}};
const key = 'sdk-test-request-123456';
const baseWork = {id:'i-1',kind:'Issue',title:'Example',description:'',status:'draft',version:1,priority:0,artifact_ids:[],external_link_ids:[]};
const baseResult = {work:baseWork,receipt:{receipt_id:'receipt-1',operation:'devgraph.work.create.v1',subject_label:'Issue',subject_id:'i-1',receipt_status:'pending',duplicate:false,correlation_id:'dg:sha256:'+'1'.repeat(64)}};

function provider() {
  let streams = new Map(), serial = 0;
  const state = {calls:[],mode:'normal',actor:'test-actor',delay:null};
  const stream = (raw,status=200) => {
    const stream_id = `stream-${++serial}`;
    const data = Buffer.from(raw);
    streams.set(stream_id,{data,at:0,seq:0});
    return {stream_id,status,content_type:status<400?'application/json':'application/problem+json',content_encoding:'identity',content_length:String(data.length),limit:8*1024*1024,dispatched:status===201};
  };
  state.devgraph = async p => {
    state.calls.push(p);
    if(p.action==='connect')return {connection_id:`connection-${++serial}`,actor_id:state.actor,receiver_profile:'302680d9f3a263a4897bc855abcebc6fa9abbca00d08c69af76757da88b07e5a',stable_issuer:'secs:test-work',audience:'devgraph://receiver-local',origin:'http://127.0.0.1:8080',capabilities:['read','work.v1']};
    if(p.action==='request_read_access')return {read_context:'read-token',expires_at:Math.floor(Date.now()/1000)+900};
    if(p.action==='authorize') {
      if(state.delay)await state.delay;
      return {authorization_id:`authorization-${++serial}`,expires_at:Math.floor(Date.now()/1000)+60};
    }
    if(p.action==='execute') {
      const response=(raw,status=200)=>({...stream(raw,status),dispatched:true});
      if(state.delay)await state.delay;
      if(state.mode==='lost')throw Error('secret internal authority');
      if(state.mode==='untrusted_error')throw {code:'private_token_value',dispatched:true};
      if(state.mode==='not_sent')throw {code:'authorization_expired',dispatched:false};
      if(state.mode==='audit')return response(JSON.stringify({type:'about:blank',title:'Audit unavailable',status:503,detail:'a commit may exist'}),503);
      if(state.mode==='invalid')return response('{"work":',201);
      if(state.mode==='duplicate')return response(JSON.stringify({...baseResult,work:null,receipt:{...baseResult.receipt,duplicate:true}}));
      return response(JSON.stringify(baseResult),201);
    }
    if(p.action==='read') {
      if(state.mode==='bad_header'){const header=stream(JSON.stringify(baseWork));header.content_encoding='gzip';return header;}
      if(state.mode==='duplicate_keys')return stream('{"id":"i-1","id":"i-2"}');
      if(p.request.kind==='list_work')return stream(JSON.stringify({items:p.request.after_id?[]:[baseWork]}));
      return stream(JSON.stringify(baseWork).replace('"version":1','"version":9223372036854775807').replace('"priority":0','"priority":-9223372036854775808'));
    }
    if(p.action==='pull') {
      if(state.pullDelay) await state.pullDelay;
      const reader=streams.get(p.stream_id);
      assert.equal(p.ack_seq,reader.seq-1);
      const chunk=reader.data.subarray(reader.at,reader.at+49152);
      reader.at+=chunk.length;
      const result={stream_id:p.stream_id,seq:reader.seq++,total:reader.at,chunk_b64:chunk.toString('base64url'),done:reader.at===reader.data.length};
      if(state.mode==='bad_seq')result.seq++;
      return result;
    }
    if(p.action==='cancel'){streams.delete(p.target_id);return {cancelled:true,dispatched:false};}
    if(p.action==='dispose'){streams.clear();return {disposed:true};}
    throw {code:'unknown_action',dispatched:false};
  };
  return state;
}
async function fixture() {
  const transport=provider(); globalThis.castaliaWallet=transport;
  const runtime=await initialize({wasm:bytes});
  const connection=await connectCastalia();
  const client=runtime.createClient({connection});
  return {transport,runtime,connection,client,async close(){runtime.dispose();await connection.dispose();delete globalThis.castaliaWallet;}};
}

test('actual WASM preserves all frozen canonical requests and rejects numeric/Unicode traps',async()=>{
  const f=await fixture();
  try {
    for(const vector of vectors){const prepared=f.client.prepareBytes(new TextEncoder().encode(vector.raw),{idempotency_key:key});assert.equal(new TextDecoder().decode(prepared.canonical_bytes),vector.canonical);assert.equal(prepared.summary.request_digest_sha256,vector.digest);prepared.dispose();}
    for(const priority of [9007199254740992,9007199254740992n,NaN,Infinity,0.5,-0])assert.throws(()=>f.client.prepare({...request,payload:{...request.payload,priority}},{idempotency_key:key}),DevgraphError);
    for(const title of ['\ud800','\udfff'])assert.throws(()=>f.client.prepare({...request,payload:{...request.payload,title}},{idempotency_key:key}),DevgraphError);
    const valid=f.client.prepare({...request,payload:{...request.payload,title:'e\u0301 é 🔒 �',priority:9007199254740991n}},{idempotency_key:key});valid.dispose();
    let getterRead=false;const invalid={...request,payload:{...request.payload,get priority(){getterRead=true;return 1}}};assert.throws(()=>f.client.prepare(invalid,{idempotency_key:key}));assert.equal(getterRead,false);
  }finally{await f.close();}
});

test('read raw i64 bytes become BigInt; explicit per-connection contexts and pagination',async()=>{
  const f=await fixture();try{
    const read_context=await f.connection.requestReadAccess();
    const work=await f.client.getWork('Issue','i-1',{read_context});
    assert.equal(work.version,9223372036854775807n);assert.equal(work.priority,-9223372036854775808n);
    assert.equal(Object.getPrototypeOf(work),Object.prototype);
    assert.deepEqual(await Array.fromAsync(f.client.iterateWork({kind:'Issue',limit:1},{read_context})),[{...baseWork,version:1n,priority:0n}]);
    await assert.rejects(f.client.getWork('Issue','i-1',{read_context:{expires_at:Date.now()+10000}}),{code:'read_context_expired'});
    f.transport.mode='duplicate_keys';await assert.rejects(f.client.getWork('Issue','i-1',{read_context}));
    f.transport.mode='bad_seq';await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'invalid_chunk'});
    await f.connection.reconnect();await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'read_context_expired'});
  }finally{await f.close();}
});

test('prepared immutable snapshots, single dispatch, unknown outcomes and same-key duplicate recovery',async()=>{
  const f=await fixture();try{
    const original=structuredClone(request), prepared=f.client.prepare(original,{idempotency_key:key});original.payload.title='changed';
    assert.equal(prepared.summary.request.payload.title,'Example');
    const snapshot=prepared.canonical_bytes;snapshot.fill(0);assert.notEqual(prepared.canonical_bytes[0],0);
    const attempt=await prepared.authorize();const committed=await attempt.execute();assert.equal(committed.kind,'committed');assert.equal(committed.work.version,1n);assert.equal(committed.receipt.receipt_status,'pending');
    assert.equal((await attempt.execute()).kind,'not_dispatched');
    for(const mode of ['lost','invalid','audit']){f.transport.mode=mode;assert.equal((await (await prepared.authorize()).execute()).kind,'outcome_unknown');}
    f.transport.mode='not_sent';assert.equal((await(await prepared.authorize()).execute()).kind,'not_dispatched');
    f.transport.mode='duplicate';const recovered=await(await prepared.authorize()).execute();assert.equal(recovered.kind,'committed');assert.equal(recovered.work,null);assert.equal(recovered.receipt.correlation_id,'dg:sha256:'+'1'.repeat(64));
    const requests=f.transport.calls.filter(c=>c.action==='authorize');assert.ok(requests.every(c=>c.idempotency_key===key&&c.request_b64===requests[0].request_b64));
    prepared.dispose();await assert.rejects(prepared.authorize(),{code:'disposed'});
  }finally{await f.close();}
});

test('cancellation and disposal prevent late authorization/dispatch; reconnect rejects changed actor',async()=>{
  const f=await fixture();try{
    const prepared=f.client.prepare(request,{idempotency_key:key}), controller=new AbortController();
    let release;f.transport.delay=new Promise(resolve=>{release=resolve});
    const pending=prepared.authorize({signal:controller.signal});controller.abort();await assert.rejects(pending,{code:'cancelled'});release();await new Promise(r=>setTimeout(r,0));
    assert.ok(f.transport.calls.some(c=>c.action==='cancel'));assert.ok(!f.transport.calls.some(c=>c.action==='execute'));
    f.transport.delay=null;const attempt=await prepared.authorize();f.client.dispose();assert.equal((await attempt.execute()).kind,'not_dispatched');
    f.transport.actor='changed';await assert.rejects(f.connection.reconnect(),{code:'connection_binding_changed'});
  }finally{await f.close();}
});

test('custom bytes and compiled modules initialize; malformed module failure can retry',async()=>{
  await assert.rejects(initialize({wasm:new Uint8Array([0,1,2])}),{code:'initialization_failed'});
  const compiled=await WebAssembly.compile(bytes);const runtime=await initialize({wasm:compiled});runtime.dispose();
  const second=await initialize({wasm:bytes});second.dispose();assert.throws(()=>new CastaliaConnection(Symbol(),()=>{},{}),{code:'invalid_connection'});
});

test('abort after execute response starts remains outcome_unknown',async()=>{
  const f=await fixture();try{
    const prepared=f.client.prepare(request,{idempotency_key:key});
    const attempt=await prepared.authorize();let release;
    f.transport.pullDelay=new Promise(resolve=>{release=resolve});
    const controller=new AbortController();const pending=attempt.execute({signal:controller.signal});
    while(!f.transport.calls.some(c=>c.action==='pull'))await new Promise(r=>setTimeout(r,0));
    controller.abort();const result=await pending;assert.equal(result.kind,'outcome_unknown');assert.equal(result.error.dispatched,true);release();
  }finally{await f.close();}
});

test('cancel before provider submission does not execute; malformed stream metadata releases reader',async()=>{
  const f=await fixture();try{
    const prepared=f.client.prepare(request,{idempotency_key:key});
    const attempt=await prepared.authorize();const controller=new AbortController();
    const result=attempt.execute({signal:controller.signal});controller.abort();
    assert.equal((await result).kind,'not_dispatched');assert.equal(f.transport.calls.filter(c=>c.action==='execute').length,0);
    const read_context=await f.connection.requestReadAccess();f.transport.mode='bad_header';
    await assert.rejects(f.client.getWork('Issue','i-1',{read_context}),{code:'invalid_stream'});
    assert.ok(f.transport.calls.some(c=>c.action==='cancel'&&c.target_id.startsWith('stream-')));
    f.transport.mode='untrusted_error';const rejected=await(await prepared.authorize()).execute();
    assert.equal(rejected.kind,'outcome_unknown');assert.equal(rejected.error.code,'bridge_failed');
    assert.ok(!JSON.stringify(rejected).includes('private_token_value'));
  }finally{await f.close();}
});
