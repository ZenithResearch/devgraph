import test from 'node:test';
import assert from 'node:assert/strict';
import {Wallet, subjectPublicKey} from '../../src/devgraph/frontend/static/kanban/wallet.mjs';
test('generic wallet support cannot enable an unsupported Devgraph workflow', async () => {
  let asked = false;
  const provider = {getCapabilities: async () => ['credential_presentation_v2'],
    presentCredential: async () => {asked = true;}, getSubject: async () => {throw Error('not needed');}};
  const wallet = new Wallet(provider, async () => ({DevgraphCredentialClient: class {},
    createDevgraphHttpTransport: () => ({getCapabilities: async () => ({
      schema: 'devgraph.credential-transport-capabilities.v2', operations: ['devgraph.work.create.v1']})})}),
    'http://127.0.0.1:8080');
  await assert.rejects(wallet.prepareSetup(), /pending Devgraph authority/);
  assert.equal(asked, false); assert.equal(wallet.connection, null);
});
test('legacy application-specific Wallet bridge is not an alternative', async () => {
  let invoked = false;
  const wallet = new Wallet({devgraph: async () => {invoked = true;}});
  await assert.rejects(wallet.prepareSetup(), /credential-bound approvals/);
  assert.equal(invoked, false);
});

test('public subject accepts raw Ed25519 and exact SPKI PEM, rejects other algorithms', () => {
  const raw = 'ab'.repeat(32);
  const encoded = Buffer.from('302a300506032b6570032100' + raw, 'hex').toString('base64');
  const pem = '-----BEGIN PUBLIC KEY-----\n' + encoded + '\n-----END PUBLIC KEY-----';
  assert.equal(subjectPublicKey({publicKey: raw}), raw);
  assert.equal(subjectPublicKey({publicKey: pem}), raw);
  assert.throws(() => subjectPublicKey({publicKey: pem.replace('MCowBQYDK2Vw', 'MCowBQYDK2Vu')}));
  assert.throws(() => subjectPublicKey({publicKey: pem.replace(encoded, encoded + 'AAAA')}));
  assert.throws(() => subjectPublicKey({publicKey: 'AB'.repeat(32)}));
});
