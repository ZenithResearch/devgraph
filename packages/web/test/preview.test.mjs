import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initialize, connectCastalia, exportJson} from '../dist/index.js';

const source = await readFile(new URL('../../../src/devgraph/frontend/sdk_preview.py', import.meta.url), 'utf8');
const match = source.match(/\nJS = """([\s\S]*?)"""/);
assert.ok(match, 'exercise the actual controlled preview script');
const script = match[1].replace(/^import \{initialize,\nconnectCastalia,\nexportJson\} from '\.\/pkg\/index\.js';/, '');
const wasm = new Uint8Array(await readFile(new URL('../dist/internal/devgraph_web_bg.wasm', import.meta.url)));

test('preview preserves the prepared request/key across lost execute and a failed reconnect', async () => {
  let connected = false, serial = 0, execution = 0, connects = 0;
  const calls = [];
  const duplicate = Buffer.from(JSON.stringify({work:null,receipt:{receipt_id:'original-receipt',operation:'devgraph.work.create.v1',subject_label:'Issue',subject_id:'i-1',receipt_status:'pending',duplicate:true,correlation_id:'dg:sha256:'+'1'.repeat(64)}}));
  globalThis.castaliaWallet = {devgraph: async message => {
    calls.push(message);
    if (message.action === 'connect') {
      if(++connects===2)throw {code:'transport_unavailable',dispatched:false};
      connected = true;
      return {connection_id:`connection-${++serial}`,actor_id:'fixture-actor',receiver_profile:'302680d9f3a263a4897bc855abcebc6fa9abbca00d08c69af76757da88b07e5a',stable_issuer:'secs:test-work',audience:'devgraph://receiver-local',origin:'http://127.0.0.1:8080',capabilities:['read','work.v1']};
    }
    if (message.action === 'dispose') { connected = false; return {disposed:true}; }
    if (message.action === 'cancel') return {cancelled:true,dispatched:execution > 0};
    if (!connected) throw {code:'connection_lost',dispatched:false};
    if (message.action === 'request_read_access') return {read_context:`read-${serial}`,expires_at:Math.floor(Date.now()/1000)+900};
    if (message.action === 'authorize') return {authorization_id:`authorization-${serial}`,expires_at:Math.floor(Date.now()/1000)+60};
    if (message.action === 'execute') {
      if (++execution === 1) { connected = false; throw {code:'connection_lost',dispatched:true}; }
      return {stream_id:'duplicate-stream',status:200,content_type:'application/json',content_encoding:'identity',content_length:String(duplicate.length),limit:8388608,dispatched:true};
    }
    if (message.action === 'pull') return {stream_id:'duplicate-stream',seq:0,total:duplicate.length,chunk_b64:duplicate.toString('base64url'),done:true};
    throw Error('unexpected fixture action');
  }};
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {disabled:true,value:'',textContent:''});
    return elements.get(id);
  };
  element('kind').value = 'Issue'; element('work-id').value = 'i-1'; element('title').value = 'Example';
  let pagehide;
  const isBusy = new Function('initialize', 'connectCastalia', 'exportJson', 'document', 'addEventListener', `${script}\nreturn () => busy;`)(
    () => initialize({wasm}), connectCastalia, exportJson, {getElementById:element}, (_name, listener) => { pagehide = listener; },
  );
  const settle = async () => {
    for (let i = 0; i < 1000 && isBusy(); i++) await new Promise(resolve => setTimeout(resolve, 1));
    assert.equal(isBusy(), false, 'preview action must settle');
  };
  try {
    element('connect').onclick(); await settle();
    element('read-access').onclick(); await settle();
    assert.equal(element('read').disabled, false);
    element('work').onsubmit({preventDefault(){}}); await settle();
    assert.equal(element('status').textContent, 'outcome_unknown');
    assert.equal(element('retry').disabled, false);
    assert.equal(calls.filter(call => call.action === 'execute').length, 1);

    element('connect').onclick(); await settle();
    assert.equal(element('status').textContent,'transport_unavailable');
    assert.equal(calls.filter(call => call.action === 'execute').length,1,'failed reconnect must not retry');
    element('connect').onclick(); await settle();
    assert.equal(element('read').disabled, true, 'reconnect invalidates old read access');
    assert.equal(calls.filter(call => call.action === 'execute').length, 1, 'reconnect must not retry');
    element('retry').onclick(); await settle();
    assert.equal(element('status').textContent, 'committed');
    const result = JSON.parse(element('result').textContent);
    assert.equal(result.work, null);
    assert.equal(result.receipt.duplicate, true);
    assert.equal(result.receipt.receipt_id, 'original-receipt');
    const authorized = calls.filter(call => call.action === 'authorize');
    assert.equal(authorized.length, 2, 'each attempt still requires explicit authorization');
    assert.equal(authorized[0].request_b64, authorized[1].request_b64);
    assert.equal(authorized[0].idempotency_key, authorized[1].idempotency_key);
    assert.notEqual(authorized[0].connection_id, authorized[1].connection_id);
  } finally {
    pagehide?.(); await new Promise(resolve => setTimeout(resolve, 0));
    delete globalThis.castaliaWallet;
  }
});
