import test from 'node:test';
import assert from 'node:assert/strict';
import {DevgraphCredentialClient} from '../../src/devgraph/frontend/static/credential-v2/coordinator.mjs';
const key = 'generic-v2-test-idempotency';
function fixture(overrides = {}) {
  const calls = [];
  const input = {schema: 'castalia.credential-presentation-request.v2',
    credential: {claims: {nonce: 'original'}}, disclosure: {title: 'Devgraph request', statements: ['exact']}};
  const provider = {getCapabilities: async () => ['credential_presentation_v2'],
    presentCredential: async () => ({state: 'approved', presentation: {schema: 'castalia.credential-presentation.v2'}}),
    ...overrides.provider};
  const transport = {prepareCredential: async (...args) => {calls.push(['prepare', ...args]); return input;},
    executeCredential: async (...args) => {calls.push(['execute', ...args]); return {state: 'committed'};},
    reconcileOperation: async (...args) => {calls.push(['reconcile', ...args]); return {state: 'committed'};},
    ...overrides.transport};
  return {client: new DevgraphCredentialClient({provider, transport, holderPublicKey: 'ab'.repeat(32),
    origin: 'http://127.0.0.1:8080'}), calls, input, provider};
}
test('only generic provider used; each use requires approval and owns its transport', async () => {
  let approvals = 0;
  const {client, calls} = fixture({provider: {presentCredential: async () => {
    approvals++; return {state: 'approved', presentation: {schema: 'castalia.credential-presentation.v2'}};
  }}});
  await client.execute('{}', key); await client.execute('{}', key + '-2');
  assert.equal(approvals, 2); assert.deepEqual(calls.map(c => c[0]), ['prepare','execute','prepare','execute']);
  assert.equal(calls[0][1].caller.id, 'http://127.0.0.1:8080');
});
test('provider mutation cannot replace saved credential during consent', async () => {
  const {client, calls} = fixture({provider: {presentCredential: async value => {
    value.credential.claims.nonce = 'substituted';
    return {state: 'approved', presentation: {schema: 'castalia.credential-presentation.v2'}};
  }}});
  await client.execute('{}', key);
  assert.equal(calls[1][1].credential.claims.nonce, 'original');
});
test('denial and unavailable capability never reach execution', async () => {
  for (const provider of [{getCapabilities: async () => []}, {presentCredential: async () => ({state: 'denied'})}]) {
    const {client, calls} = fixture({provider});
    await assert.rejects(client.execute('{}', key));
    assert.equal(calls.filter(c => c[0] === 'execute').length, 0);
  }
});
test('disposal while consent is pending rejects late approval without dispatch', async () => {
  let approve; const waiting = new Promise(resolve => {approve = resolve;});
  const {client, calls} = fixture({provider: {presentCredential: () => waiting}});
  const pending = client.execute('{}', key);
  await new Promise(resolve => setTimeout(resolve, 0));
  client.dispose(); approve({state: 'approved', presentation: {schema: 'castalia.credential-presentation.v2'}});
  await assert.rejects(pending, {code: 'disposed', dispatched: false});
  assert.equal(calls.filter(c => c[0] === 'execute').length, 0);
});
test('lost response blocks fresh mutation until operation-status reconciliation', async () => {
  const {client, calls} = fixture({transport: {executeCredential: async () => {throw Error('lost');}}});
  await assert.rejects(client.execute('{}', key), {code: 'outcome_unknown', dispatched: true});
  await assert.rejects(client.execute('{}', key + '-new'), {code: 'reconciliation_required'});
  await client.reconcile();
  assert.equal(calls.at(-1)[0], 'reconcile'); assert.equal(calls.at(-1)[1].idempotency_key, key);
  assert.equal(calls.filter(c => c[0] === 'prepare').length, 2, 'recovery obtains a fresh credential and approval');
});
test('no insecure remote origin or missing transport accepted', () => {
  assert.throws(() => new DevgraphCredentialClient({provider: {}, transport: {},
    holderPublicKey: 'ab'.repeat(32), origin: 'http://remote.example'}));
});
