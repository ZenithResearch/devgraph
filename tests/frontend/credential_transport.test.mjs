import test from 'node:test';
import assert from 'node:assert/strict';
import {createDevgraphHttpTransport} from '../../src/devgraph/frontend/static/credential-v2/transport.mjs';
test('transport uses same-origin JSON without bearer cookies or redirects', async () => {
  const calls = [];
  const transport = createDevgraphHttpTransport({origin: 'http://127.0.0.1:8080',
    fetch: async (...args) => {calls.push(args); return new Response('{"state":"committed"}',
      {headers: {'Content-Type': 'application/json'}});}});
  await transport.prepareCredential({request: 'exact'});
  await transport.executeCredential({presentation: 'proof'});
  await transport.reconcileOperation({request: 'original'});
  assert.deepEqual(calls.map(([url]) => url.split('/').at(-1)), ['prepare', 'execute', 'status']);
  for (const [, options] of calls) {
    assert.equal(options.mode, 'same-origin'); assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit'); assert.deepEqual(options.headers, {'Content-Type': 'application/json'});
  }
});
test('bounded response bytes and HTTP metadata reach the strict decoder unchanged', async () => {
  const raw = '{"state":"unknown","state":"committed"}';
  for (const status of [200, 409, 412, 503]) {
    const transport=createDevgraphHttpTransport({origin:'https://application.example',fetch:async()=>
      new Response(raw,{status,headers:{'content-type':'application/problem+json'}})});
    const result=await transport.executeCredential({});
    assert.equal(new TextDecoder().decode(result.transport_response_bytes),raw);
    assert.equal(result.transport_response_metadata.status,status);
    assert.equal(result.state,undefined);
  }
  const transport=createDevgraphHttpTransport({origin:'https://application.example',fetch:async()=>
    new Response('a'.repeat(262145))});
  await assert.rejects(transport.executeCredential({}),/response_too_large/);
});
