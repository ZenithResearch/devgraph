import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {verifyServiceRoute} from '../../src/devgraph/frontend/static/credential-v2/service-route.mjs';
const fixture=JSON.parse(await readFile(new URL('../fixtures/service-access-point.json',import.meta.url)));
const {binding,pin}=fixture;
const options={origin:binding.claims.origin,service:pin.service,now:102,profile:{presentations:[{issuer:pin.issuer,key_id:pin.key_id,public_key:pin.public_key,audience:'devgraph',callers:[{kind:'browser',id:binding.claims.origin}]}]}};
test('shared signature authenticates a route without extending member scope',async()=>{
 assert.deepEqual(await verifyServiceRoute({schema:'devgraph.service-access-point.v1',binding},options),binding);
 for(const changed of [{origin:'https://evil.example'},{service:'service:other'},{now:1000}])await assert.rejects(verifyServiceRoute({schema:'devgraph.service-access-point.v1',binding},{...options,...changed}));
 const bad=structuredClone(binding);bad.claims.authority_receiver='https://evil.example';
 await assert.rejects(verifyServiceRoute({schema:'devgraph.service-access-point.v1',binding:bad},options));
});

test('browser keeps site consent but uses the canonical read/request route',async()=>{
 const {WalletReadClient}=await import('../../src/devgraph/frontend/static/credential-v2/read-access.mjs');
 const origin=binding.claims.origin, calls=[];
 const config={schema:'devgraph.wallet-read-configuration.v1',endpoint:'/monitor/wallet-access/v1',holder_grants:{schema:'secs.holder-grant.v1',service:pin.service,endpoint:'/monitor/wallet-access/v1/grants'},provider_profile:{schema:'castalia.provider-profile.v1',display_name:'Fixture',origins:[origin],membership:null,presentations:options.profile.presentations},service_access_point_endpoint:'/.well-known/devgraph-service.json'};
 const client=new WalletReadClient({origin,now:()=>102,fetchImpl:async function(path,request){
  assert.equal(this,undefined,'browser fetch must not receive the WalletReadClient as its receiver');
  calls.push(path);
  if(path==='/monitor/wallet-access/v1')return Response.json(config);
  if(path==='/.well-known/devgraph-service.json')return Response.json({schema:'devgraph.service-access-point.v1',binding});
  if(path==='/service/v1/monitor/wallet-access/v1/requests')return Response.json({items:[]});
  throw Error('Unexpected route: '+path);
 }});
 await client.initialize();
 let approvedOrigin;
 const provider={getCapabilities:async()=>['provider_profiles_v1','connection_v1','graph_access_v2','holder_grant_v1','read_setup_status_v1','service_access_point_v1'],requestConnection:async()=>({state:'connected'}),proposeProviderProfile:async(profile)=>{approvedOrigin=profile.origins[0];return {state:'approved'};},readAccess:async(request)=>{
  if(request.operation==='setup_status')return {schema:'castalia.read-setup-status.v1',connection_approved:false,profile_approved:false};
  if(request.operation==='identity')return {subject_public_key:'11'.repeat(32),installation_id:'22'.repeat(16)};
  if(request.operation==='use_grant')return {accessId:'fixture',service:pin.service,expires_at:202};
  if(request.operation==='present_grant')return {credential:{},proof:{},call:request.call};
  throw Error(request.operation);
 }};
 // Approve the public proposal on the actual site; simulate the separately
 // approved connection so this test focuses on authority/transport separation.
 await client.useProvider(provider);await client.setup.approve();
 assert.equal(approvedOrigin,origin);
 client.provider=provider;client.graphAccessV2=true;client.holderGrants=true;
 await client.identifyAndCheckAccess(()=>{});await client.advance();
 assert.deepEqual(await client.read('/todos/v2?kind=Task'),{items:[]});
 assert.equal(client.authorityOrigin,binding.claims.authority_receiver);
 assert.equal(calls.at(-1),'/service/v1/monitor/wallet-access/v1/requests');
});
