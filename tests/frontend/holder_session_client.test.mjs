import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {WalletReadClient} from '../../src/devgraph/frontend/static/credential-v2/read-access.mjs';
import {canonicalSessionJson} from '../../src/devgraph/frontend/static/credential-v2/holder-session.mjs';
const hex=b=>Buffer.from(b).toString('hex');
const bytes=(domain,value)=>new TextEncoder().encode(domain+'\0'+canonicalSessionJson(value));
async function fixture({lost=false,supported=true,operation='devgraph.work.list',legacyParent=false,badToken=false,badStatus=false,advertisedV2=false,fresh=false}={}){
 let now=1000,locked=false,approvals=0,preparations=0,reads=0,statuses=0,credential,challenge;
 let saved=!fresh;const enrollments=[];
 const nonces=new Set(),issuer=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const origin='https://graph.example',service='service:graph',pin={issuer:'synthetic',key_id:'key',public_key:hex(await webcrypto.subtle.exportKey('raw',issuer.publicKey)),service};
 const profile={schema:'castalia.provider-profile.v1',display_name:'Synthetic issuer',origins:[origin],membership:null,presentations:[{...pin,audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};delete profile.presentations[0].service;
 const configuration={schema:'devgraph.wallet-read-configuration.v1',endpoint:'/monitor/wallet-access/v1',provider_profile:profile,holder_grants:{schema:'secs.holder-grant.v2',service,endpoint:'/monitor/wallet-access/v1/grants'}};
 if(advertisedV2)Object.assign(configuration.holder_grants,{schema:'secs.holder-grant.v1',v2_endpoint:'/monitor/wallet-access/v1/grants/v2',supported_schemas:['secs.holder-grant.v1','secs.holder-grant.v2']});
 const capabilities={schema:'secs.holder-session-capabilities.v1',supported,profile:'secs.holder-session.v1',issuer_pin:pin,audience:'devgraph',grant_schemas:['secs.holder-grant.v2'],credential_schema:'secs.holder-session-credential.v1',presentation_schema:'secs.holder-session-presentation.v1',read_only:true,max_ttl_seconds:600,review_ttl_seconds:120,proof_max_ttl_seconds:60,challenge_path:'/holder-sessions/v1/challenges',exchange_path:'/holder-sessions/v1/exchange',status_path:'/holder-sessions/v1/status'};
 const provider={getCapabilities:async()=>['read_access_v1','provider_profiles_v1','connection_v1','holder_grant_v1','holder_grant_v2','holder_session_exchange_v1'],proposeProviderProfile:async()=>({state:'approved'}),requestConnection:async()=>({state:'connected'}),readAccess:async input=>{
  if(locked)throw Error('wallet_locked');
  if(input.operation==='identity')return{subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),memberships:[]};
  if(input.operation==='prepare_holder_session'){
   preparations++;
   if(!saved)throw Error('grant_not_saved');
   if(legacyParent)throw Error('holder_session_parent_unsupported');
   challenge={schema:'secs.holder-session-challenge.v1',challenge_id:'c'.repeat(32),request_id:'d'.repeat(32),nonce:'e'.repeat(64),issuer:pin.issuer,key_id:pin.key_id,service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'2'.repeat(64),holder_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),session_installation_id:input.session_installation_id,session_public_key:input.session_public_key,permissions:[{operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph',constraints:{},roles:[]}],issued_at:now,review_deadline:now+60,expires_at:now+60};
   challenge.permissions[0].operation=operation;
   return{schema:'castalia.holder-session-prepared.v1',preparationId:'opaque',challenge};
  }
  if(input.operation==='open_holder_session'){
   approvals++;assert.deepEqual(input.challenge,challenge);
   const key=await webcrypto.subtle.importKey('raw',Buffer.from(challenge.session_public_key,'hex'),'Ed25519',false,['verify']);
   assert.ok(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(input.session_signature,'hex'),bytes('secs.holder-session-possession.v1/signature',challenge)));
   const claims={schema:'secs.holder-session-credential.v1',credential_id:'2'.repeat(32),challenge_digest:hex(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalSessionJson(challenge)))),challenge};
   credential={claims,signature:hex(await webcrypto.subtle.sign('Ed25519',issuer.privateKey,bytes('secs.holder-session-credential.v1/signature',claims)))};
   if(lost)throw Error('holder_session_outcome_unknown');
   if(badToken)credential.signature='0'.repeat(128);
   return{schema:'castalia.holder-session-opened.v1',credential};
  }
  if(input.operation==='approve')return{accessId:'enrollment',enrollment:{challenge:input.challenge,signature:'original',proof:{}}};
  if(input.operation==='disconnect')return{};
  if(input.operation==='save_grant'){saved=true;return{accessId:'stored-grant',service,expires_at:1060};}
  if(input.operation==='use_grant'){if(!saved)throw Error('grant_not_saved');return{accessId:'legacy',service,expires_at:1060};}
  throw Error('unexpected Wallet call '+input.operation);
 }};
 const fetchImpl=async(url,options={})=>{
  assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
  let value=configuration;
  if(url.endsWith('/capabilities'))value=capabilities;
  if(url==='/monitor/wallet-access/v1/challenges')value={receiver:origin,expires_at:1120};
  if(url.endsWith('/grants/v2')){enrollments.push({url,body:options.body});value={schema:'devgraph.holder-grant-result.v1',grant:{claims:{schema:'secs.holder-grant.v2',service}}};}
  if(url.endsWith('/status')){
   statuses++;const request=JSON.parse(options.body),proof={...request};delete proof.signature;
   assert.equal(request.request_id,challenge.request_id);assert.equal(request.challenge_id,challenge.challenge_id);
   const key=await webcrypto.subtle.importKey('raw',Buffer.from(challenge.session_public_key,'hex'),'Ed25519',false,['verify']);
   assert.ok(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(request.signature,'hex'),bytes('secs.holder-session-status.v1/signature',proof)));
   value={schema:'secs.holder-session-result.v1',request_id:challenge.request_id,challenge_id:challenge.challenge_id,state:'issued',expires_at:challenge.expires_at+(badStatus?1:0),credential};
  }
  if(url.endsWith('/requests')){
   const envelope=JSON.parse(options.body),proof={...envelope.proof};delete proof.signature;
   assert.equal(proof.schema,'secs.holder-session-presentation.v1');assert.equal(proof.expires_at,Math.min(now+60,1060));
   assert.equal(nonces.has(proof.nonce),false);nonces.add(proof.nonce);
   const key=await webcrypto.subtle.importKey('raw',Buffer.from(challenge.session_public_key,'hex'),'Ed25519',false,['verify']);
   assert.ok(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(envelope.proof.signature,'hex'),bytes('secs.holder-session-presentation.v1/signature',proof)));
   reads++;value={items:[]};
  }
  return{ok:true,status:200,json:async()=>structuredClone(value)};
 };
 const client=new WalletReadClient({origin,fetchImpl,now:()=>now});
 const connect=async()=>{await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();};
 return{client,provider,connect,enrollments,lock:()=>{locked=true},now:value=>{now=value},counts:()=>({approvals,preparations,reads,statuses})};
}
test('one Wallet approval opens a finite page session; repeated reads continue after Wallet locks',async()=>{
 const f=await fixture();await f.connect();assert.equal(f.client.stage,'session_review');assert.equal(f.counts().approvals,0);
 await f.client.advance();assert.equal(f.client.stage,'ready');assert.equal(f.client.usingClientSession,true);assert.equal(f.client.expiresAt,1060);
 f.lock();await f.client.read('/monitor/topology/v1');await f.client.read('/todos/v2?limit=1');f.now(1059);await f.client.read('/todos/v2?limit=1');
 assert.equal(f.client.expiresAt,1060);assert.deepEqual(f.counts(),{approvals:1,preparations:1,reads:3,statuses:0});
 f.now(1060);assert.equal(f.client.expireIfNeeded(),true);await assert.rejects(f.client.read('/todos/v2?limit=1'));assert.equal(f.counts().approvals,1);
});
test('unknown exchange recovers the original credential with the same key and no new approval',async()=>{
 const f=await fixture({lost:true});await f.connect();await assert.rejects(f.client.advance(),/holder_session_outcome_unknown/);
 assert.equal(f.client.stage,'session_recovery');f.lock();await f.client.advance();assert.equal(f.client.stage,'ready');await f.client.read('/todos/v2?limit=1');
 assert.deepEqual(f.counts(),{approvals:1,preparations:1,reads:1,statuses:1});
});
test('disabled server session capability keeps the existing saved-grant path',async()=>{
 const f=await fixture({supported:false});await f.connect();assert.equal(f.client.stage,'saved_grant');await f.client.advance();assert.equal(f.client.stage,'ready');assert.equal(f.client.usingClientSession,false);assert.equal(f.counts().preparations,0);
});
test('a verified v1 saved parent keeps its existing connection path before session consent',async()=>{
 const f=await fixture({legacyParent:true});await f.connect();assert.equal(f.client.stage,'saved_grant');await f.client.advance();assert.equal(f.client.stage,'ready');assert.equal(f.client.usingClientSession,false);assert.equal(f.counts().approvals,0);
});
test('an additive v2 endpoint enables sessions without changing the server legacy default',async()=>{
 const f=await fixture({advertisedV2:true});await f.connect();assert.equal(f.client.configuration.holder_grants.schema,'secs.holder-grant.v1');assert.equal(f.client.stage,'session_review');await f.client.advance();assert.equal(f.client.usingClientSession,true);
});
test('an existing v1 parent remains usable and offers explicit new session setup',async()=>{
 const f=await fixture({advertisedV2:true,legacyParent:true});await f.connect();assert.equal(f.client.stage,'saved_grant');assert.equal(f.client.canSetUpSession,true);assert.equal(f.counts().approvals,0);await f.client.advance();assert.equal(f.client.usingHolderGrant,true);
});
test('fresh setup saves the negotiated v2 parent once, then separately reviews its finite session',async()=>{
 const f=await fixture({advertisedV2:true,fresh:true});await f.connect();assert.equal(f.client.canSetUpSession,true);
 await f.client.setUpSession();assert.equal(f.client.stage,'approval');await f.client.advance();assert.equal(f.client.stage,'grant');assert.equal(f.enrollments.length,1);assert.equal(f.enrollments[0].url,'/monitor/wallet-access/v1/grants/v2');
 await f.client.advance();assert.equal(f.client.stage,'session_review');assert.equal(f.client.usingHolderGrant,false);assert.equal(f.counts().approvals,0);
 await f.client.advance();assert.equal(f.client.usingClientSession,true);assert.equal(f.counts().approvals,1);assert.equal(f.enrollments.length,1);
});
for(const operation of ['devgraph.work.patch','access.decide','devgraph.work.read-everything'])test('browser rejects '+operation+' before requesting session approval',async()=>{
 const f=await fixture({operation});await assert.rejects(f.connect(),/invalid_holder_session/);assert.equal(f.counts().approvals,0);assert.equal(f.client.clientSession,null);
});
test('an invalid returned token closes custody without switching to the old signer',async()=>{
 const f=await fixture({badToken:true});await f.connect();await assert.rejects(f.client.advance(),/invalid_holder_session/);assert.equal(f.client.stage,'choose');assert.equal(f.client.clientSession,null);assert.equal(f.counts().approvals,1);
});
test('status cannot change the original session expiry or silently start again',async()=>{
 const f=await fixture({lost:true,badStatus:true});await f.connect();await assert.rejects(f.client.advance(),/holder_session_outcome_unknown/);await assert.rejects(f.client.advance(),/holder_session_outcome_unknown/);assert.equal(f.client.stage,'session_recovery');assert.equal(f.counts().preparations,1);assert.equal(f.counts().approvals,1);
});
