import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeWalletError} from '../../src/devgraph/frontend/static/credential-v2/wallet-errors.mjs';

test('known old and new Wallet lock errors become actionable bounded errors',()=>{
 for(const input of [Error('wallet_unlock_required'),Error('Wallet not set up or locked for this session'),
   Object.assign(Error('irrelevant'),{code:'wallet_unlock_required'})]){
  const value=normalizeWalletError(input);assert.equal(value.code,'wallet_unlock_required');
  assert.equal(value.dispatched,false);assert.match(value.message,/Unlock/);
 }
 assert.equal(normalizeWalletError(Error('origin_not_approved')).code,'wallet_connection_required');
 assert.equal(normalizeWalletError(Error('approval_required')).code,'approval_required');
});
test('unknown causes and accessors are never exposed or invoked',()=>{
 assert.equal(normalizeWalletError(Error('secret arbitrary cause')),null);
 let calls=0;const hostile={get code(){calls++;throw Error('secret')},get message(){calls++;throw Error('secret')}};
 assert.equal(normalizeWalletError(hostile),null);assert.equal(calls,0);
 assert.equal(normalizeWalletError(new Proxy({}, {getOwnPropertyDescriptor(){throw Error('secret')}})),null);
});
