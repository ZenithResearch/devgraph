import test from 'node:test';
import assert from 'node:assert/strict';
import {WalletReadClient} from '../../src/devgraph/frontend/static/credential-v2/read-access.mjs';
const BASE='/monitor/wallet-access/v1';
const holder=()=>({schema:'secs.holder-grant.v1',service:'service:graph',endpoint:BASE+'/grants',v2_endpoint:BASE+'/grants/v2',supported_schemas:['secs.holder-grant.v1','secs.holder-grant.v2']});
const configuration=h=>({schema:'devgraph.wallet-read-configuration.v1',endpoint:BASE,holder_grants:h});
test('new discovery is additive and rejects untrusted grant routes or incomplete negotiation',async()=>{
 for(const change of [h=>h,h=>{delete h.v2_endpoint;return h},h=>({...h,v2_endpoint:'https://other.example/grants'}),h=>({...h,v2_endpoint:BASE+'/grants/v3'}),h=>({...h,supported_schemas:['secs.holder-grant.v2']}),h=>({...h,arbitrary_url:'/grants'})]){
  const h=change(holder()),client=new WalletReadClient({fetchImpl:async()=>({ok:true,status:200,json:async()=>configuration(h)})});
  if(Object.keys(h).sort().join(',')===Object.keys(holder()).sort().join(',')&&h.v2_endpoint===BASE+'/grants/v2'&&h.supported_schemas.length===2)await client.initialize();
  else await assert.rejects(client.initialize(),/wallet_read_setup_required/);
 }
});
test('uncertain grant recovery retains the original enrollment and chosen protocol endpoint',async()=>{
 const calls=[],client=new WalletReadClient({fetchImpl:async(path,{body})=>{calls.push({path,body});return{ok:true,status:200,json:async()=>({schema:'devgraph.holder-grant-result.v1',grant:{claims:{service:'service:graph',schema:'secs.holder-grant.v2'}}})};}});
 client.configuration=configuration(holder());client.originalGrantRequest={challenge:{request_id:'original'},proof:{},signature:'original-signature'};client.originalGrantEndpoint=BASE+'/grants/v2';client.originalGrantSchema='secs.holder-grant.v2';
 await client.obtainHolderGrant(()=>{});await client.obtainHolderGrant(()=>{});
 assert.deepEqual(calls,[{path:BASE+'/grants/v2',body:JSON.stringify(client.originalGrantRequest)},{path:BASE+'/grants/v2',body:JSON.stringify(client.originalGrantRequest)}]);assert.equal(client.stage,'grant');
});
test('a result from the other grant protocol cannot replace the reviewed request',async()=>{
 const client=new WalletReadClient({fetchImpl:async()=>({ok:true,status:200,json:async()=>({schema:'devgraph.holder-grant-result.v1',grant:{claims:{service:'service:graph',schema:'secs.holder-grant.v1'}}})})});
 client.configuration=configuration(holder());client.originalGrantRequest={challenge:{request_id:'original'}};client.originalGrantEndpoint=BASE+'/grants/v2';client.originalGrantSchema='secs.holder-grant.v2';
 await assert.rejects(client.obtainHolderGrant(()=>{}),/holder_grant_outcome_unknown/);assert.equal(client.stage,'grant_recovery');assert.equal(client.pendingGrant,undefined);
});
