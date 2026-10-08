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
test('bounded responses and wrong content types never become a committed result', async () => {
  for (const response of [new Response('a'.repeat(262145), {headers: {'Content-Type': 'application/json'}}),
    new Response('{}', {headers: {'Content-Type': 'text/html'}}), new Response('{}', {status: 403})]) {
    const transport = createDevgraphHttpTransport({origin: 'https://application.example', fetch: async () => response});
    await assert.rejects(transport.executeCredential({}));
  }
});
test('only the exact execute-version conflict is a known rejection', async () => {
  for (const [action,problem,expected] of [
    ['executeCredential',{type:'about:blank',status:412,title:'Version precondition failed'},true],
    ['executeCredential',{type:'about:blank',status:412,title:'Unknown'},false],
    ['reconcileOperation',{type:'about:blank',status:412,title:'Version precondition failed'},false],
  ]) {
    const transport=createDevgraphHttpTransport({origin:'http://127.0.0.1:8080',fetch:async()=>
      new Response(JSON.stringify(problem),{status:412,headers:{'content-type':'application/problem+json'}})});
    if(expected)assert.deepEqual(await transport[action]({}),{state:'rejected',code:'version_conflict',status:412});
    else await assert.rejects(transport[action]({}));
  }
});
