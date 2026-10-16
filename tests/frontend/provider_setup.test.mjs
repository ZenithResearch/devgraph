import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareProviderSetup} from '../../src/devgraph/frontend/static/credential-v2/provider-setup.mjs';
const origin='http://127.0.0.1:8080';
const profile=()=>({schema:'castalia.provider-profile.v1',display_name:'Devgraph local authority',
  origins:[origin],membership:null,presentations:[{issuer:'secs://devgraph-work',key_id:'test-key',
    public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:origin}]}]});
function fixture(){
  const calls=[];let current=true;
  const provider={getCapabilities:async()=>['credential_presentation_v2','provider_profiles_v1','connection_v1'],
    requestConnection:()=>{calls.push('connect');return Promise.resolve({state:'connected'});},
    proposeProviderProfile:p=>{calls.push(p);return Promise.resolve({state:'approved'});}};
  const transport={getProviderProfile:async()=>profile()};
  return {calls,provider,transport,origin,current:()=>{if(!current)throw Error('disposed');},dispose:()=>{current=false;}};
}
test('setup preloads public configuration; each ceremony starts synchronously from a separate click',async()=>{
  const f=fixture(),setup=await prepareProviderSetup(f);
  assert.deepEqual(f.calls,[]);
  setup.profile.presentations[0].public_key='cd'.repeat(32);
  const approval=setup.approve();assert.deepEqual(f.calls,[profile()]);await approval;
  const connection=setup.connect();assert.equal(f.calls.at(-1),'connect');await connection;
});
test('unsupported Wallets and broadened profiles fail before consent',async()=>{
  const f=fixture();f.provider.getCapabilities=async()=>['credential_presentation_v2'];
  await assert.rejects(prepareProviderSetup(f),{code:'wallet_upgrade_required'});
  assert.deepEqual(f.calls,[]);
  for(const mutate of [p=>p.origins.push('https://other.test'),p=>p.membership={},
    p=>p.presentations[0].callers.push({kind:'terminal',id:'other'}),
    p=>p.presentations[0].public_key='bad',p=>p.extra='secret',p=>p.presentations.push(p.presentations[0])]){
    const g=fixture();g.transport.getProviderProfile=async()=>{const p=profile();mutate(p);return p;};
    await assert.rejects(prepareProviderSetup(g),{code:'wallet_provider_setup_invalid'});
    assert.deepEqual(g.calls,[]);
  }
});
test('denial and disposed/late setup cannot enable writes',async()=>{
  const f=fixture();f.provider.proposeProviderProfile=async()=>({state:'denied'});
  const setup=await prepareProviderSetup(f);
  await assert.rejects(setup.approve(),{code:'user_denied'});
  let release;f.provider.requestConnection=()=>new Promise(r=>release=r);
  const pending=setup.connect();f.dispose();release({state:'connected'});
  await assert.rejects(pending,/disposed/);
  assert.throws(()=>setup.approve(),/disposed/);
});
