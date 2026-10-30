import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {WalletLoginClient} from '../../src/devgraph/frontend/static/credential-v2/session-login.mjs';
import {canonicalSessionJson} from '../../src/devgraph/frontend/static/credential-v2/holder-session.mjs';
const hex=value=>Buffer.from(value).toString('hex');
const bytes=(domain,value)=>new TextEncoder().encode(domain+'\0'+canonicalSessionJson(value));

async function fixture({lost=false,cancel=false,maximum=900,acquisition=false,membership=undefined}={}){
 let now=1000,challenge,credential,state=null,statusCode=200,corruptStatus=false;const calls=[],rows=new Map();
 const issuer=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const origin='https://graph.example',service='service:graph';
 const pin={service,issuer:'fixture',key_id:'key',public_key:hex(await webcrypto.subtle.exportKey('raw',issuer.publicKey))};
 const profile={schema:'castalia.provider-profile.v1',display_name:'Fixture',origins:[origin],membership:null,presentations:[{issuer:pin.issuer,key_id:pin.key_id,public_key:pin.public_key,audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};
 const configuration={schema:'devgraph.wallet-read-configuration.v1',endpoint:'/monitor/wallet-access/v1',web_wallet_url:null,provider_profile:profile,holder_grants:{schema:'secs.holder-grant.v2',service,endpoint:'/monitor/wallet-access/v1/grants'}};
 if(acquisition){Object.assign(configuration.holder_grants,{v2_endpoint:'/monitor/wallet-access/v1/grants/v2',supported_schemas:['secs.holder-grant.v1','secs.holder-grant.v2']});configuration.access_requests_endpoint='/devgraph/access-requests/v2';if(membership!==undefined)configuration.membership_requirement=membership;}
 const capabilities={schema:'secs.holder-session-capabilities.v1',supported:true,profile:'secs.holder-session.v1',issuer_pin:pin,audience:'devgraph',grant_schemas:['secs.holder-grant.v2'],credential_schema:'secs.holder-session-credential.v1',presentation_schema:'secs.holder-session-presentation.v1',read_only:true,max_ttl_seconds:900,review_ttl_seconds:120,proof_max_ttl_seconds:60,challenge_path:'/holder-sessions/v1/challenges',exchange_path:'/holder-sessions/v1/exchange',status_path:'/holder-sessions/v1/status'};
 capabilities.max_ttl_seconds=maximum;
 const store={load:async slot=>structuredClone(rows.get(slot)??null),reserve:async(slot,epoch)=>{rows.set(slot,{epoch,value:null});},save:async(slot,epoch,value)=>{if(rows.get(slot)?.epoch!==epoch)throw Error('browser_session_changed');rows.set(slot,structuredClone({epoch,value}));},remove:async(slot,epoch)=>{if(!epoch||rows.get(slot)?.epoch===epoch)rows.delete(slot);}};
 const provider={getCapabilities:async()=>['session_login_v1'],readAccess:async input=>{
  calls.push(input.operation);
  if(input.operation==='open_session_login'){
   challenge={schema:'secs.holder-session-challenge.v1',challenge_id:'c'.repeat(32),request_id:'d'.repeat(32),nonce:'e'.repeat(64),issuer:pin.issuer,key_id:pin.key_id,service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'2'.repeat(64),holder_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),session_installation_id:input.session_installation_id,session_public_key:input.session_public_key,permissions:[{operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph'}],issued_at:now,review_deadline:now+120,expires_at:now+900};
   return{schema:'castalia.session-login-prepared.v1',loginId:input.loginId,challenge};
  }
  if(input.operation==='cancel_session_login')return{schema:'castalia.session-login-cancelled.v1',dispatched:false};
  assert.equal(input.operation,'complete_session_login');
  if(cancel)throw Object.assign(Error('session_login_cancelled'),{code:'session_login_cancelled',stage:'consent',dispatched:false});
  const key=await webcrypto.subtle.importKey('raw',Buffer.from(challenge.session_public_key,'hex'),'Ed25519',false,['verify']);
  assert.equal(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(input.session_signature,'hex'),bytes('secs.holder-session-possession.v1/signature',challenge)),true);
  const claims={schema:'secs.holder-session-credential.v1',credential_id:'3'.repeat(32),challenge_digest:hex(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalSessionJson(challenge)))),challenge};
  credential={claims,signature:hex(await webcrypto.subtle.sign('Ed25519',issuer.privateKey,bytes('secs.holder-session-credential.v1/signature',claims)))};
  if(lost)throw Error('holder_session_outcome_unknown');
  return{schema:'castalia.holder-session-opened.v1',credential};
 }};
 const fetchImpl=async(path,options)=>{
  const value=String(path).endsWith('/capabilities')?capabilities:String(path).endsWith('/status')?{schema:'secs.holder-session-result.v1',request_id:challenge.request_id,challenge_id:challenge.challenge_id,state:state??(credential?'issued':'pending'),credential:state?null:credential??null,expires_at:challenge.expires_at}:String(path).endsWith('/requests')?{items:[]}:configuration;
  if(String(path).endsWith('/status')&&corruptStatus&&value.credential)value.credential={...value.credential,signature:'0'.repeat(128)};
  if(String(path).endsWith('/status'))calls.push('issuer_status');
  return new Response(JSON.stringify(value),{status:String(path).endsWith('/status')?statusCode:200,headers:{'Content-Type':'application/json'}});
 };
 const options={origin,fetchImpl,now:()=>now,cryptoImpl:webcrypto,store};
 const make=()=>new WalletLoginClient(options);
 return{client:make(),make,provider,calls,rows,store,now:value=>{now=value;},status:(value,code=200)=>{state=value;statusCode=code;},corruptStatus:value=>{corruptStatus=value;}};
}

test('one browser click opens one popup and completes the same finite delegation',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);
 const pending=f.client.login();assert.deepEqual(f.calls,['open_session_login']);await pending;
 assert.equal(f.client.stage,'ready');assert.equal(f.client.expiresAt,1900);
 assert.deepEqual(f.calls,['open_session_login','complete_session_login']);assert.equal(f.rows.size,1);
 const snapshot=[...f.rows.values()][0].value;assert.equal(snapshot.privateKey.extractable,false);assert.equal('parent_grant' in snapshot,false);
});
test('reload checks the original issuer result and resumes without any Wallet call',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();f.client.disconnect();f.now(1200);
 const resumed=f.make();assert.equal(await resumed.resumeLogin(),true);assert.equal(resumed.stage,'ready');assert.equal(resumed.expiresAt,1900);
 assert.deepEqual(f.calls,['open_session_login','complete_session_login','issuer_status']);
 await resumed.read('/todos/v2');assert.equal(resumed.expiresAt,1900);
});
test('known cancellation clears child custody without requesting a grant',async()=>{
 const f=await fixture({cancel:true});await f.client.prepareLogin(f.provider);await assert.rejects(f.client.login(),/session_login_cancelled/);
 assert.equal(f.client.clientSession,null);assert.equal(f.rows.size,0);assert.equal(f.calls.includes('request'),false);
});
test('lost committed reply keeps the original child and recovers the same session',async()=>{
 const f=await fixture({lost:true});await f.client.prepareLogin(f.provider);await assert.rejects(f.client.login(),/holder_session_outcome_unknown/);
 assert.equal(f.client.stage,'session_recovery');assert.equal(f.rows.size,1);await f.client.reconcileLogin();
 assert.equal(f.client.stage,'ready');assert.equal(f.client.expiresAt,1900);
 assert.deepEqual(f.calls,['open_session_login','complete_session_login','issuer_status']);
});
test('unsupported Wallet never opens a popup or reserves browser custody',async()=>{
 const f=await fixture();f.provider.getCapabilities=async()=>[];
 await assert.rejects(f.client.prepareLogin(f.provider),/wallet_update_required/);assert.equal(f.calls.length,0);assert.equal(f.rows.size,0);
});
test('logout fences a delayed result and cannot resurrect a browser session',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);const read=f.provider.readAccess;let release;
 f.provider.readAccess=input=>input.operation==='complete_session_login'?new Promise(resolve=>{release=()=>read(input).then(resolve)}):read(input);
 const pending=f.client.login();while(!release)await new Promise(resolve=>setImmediate(resolve));
 await f.client.logout();release();await assert.rejects(pending,/wallet_connection_changed/);
 assert.equal(f.rows.size,0);assert.equal(f.client.clientSession,null);assert.notEqual(f.client.stage,'ready');
});
test('an expired unissued review ends recovery and permits a fresh explicit login',async()=>{
 const f=await fixture({lost:true});await f.client.prepareLogin(f.provider);await assert.rejects(f.client.login());
 f.now(1120);f.status('expired');assert.equal(await f.client.reconcileLogin(),false);
 assert.equal(f.rows.size,0);assert.equal(f.client.clientSession,null);assert.equal(f.client.notice,'read_access_expired');
 assert.equal(f.calls.filter(c=>c==='open_session_login').length,1);
 await f.client.prepareLogin(f.provider);assert.equal(f.client.stage,'login_ready');
 assert.equal(f.calls.filter(c=>c==='open_session_login').length,1,'preparation never opens or renews a session');
});
test('generic status denial preserves the original recovery key without another Wallet call',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();f.client.disconnect();
 f.status('revoked',401);const restored=f.make();assert.equal(await restored.resumeLogin(),false);
 assert.equal(f.rows.size,1);assert.ok(restored.clientSession);assert.equal(restored.stage,'session_recovery');
 await assert.rejects(restored.read('/todos/v2'),/wallet_read_access_required/);
 f.status(null);assert.equal(await restored.reconcileLogin(),true);assert.equal(restored.expiresAt,1900);
 assert.equal(f.calls.filter(c=>c==='open_session_login').length,1);
});
test('an authenticated terminal revocation retires local custody without requesting a replacement',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();f.client.disconnect();
 f.status('revoked');const restored=f.make();assert.equal(await restored.resumeLogin(),false);
 assert.equal(f.rows.size,0);assert.equal(restored.clientSession,null);assert.equal(restored.notice,'read_access_revoked');
 assert.equal(f.calls.filter(c=>c==='open_session_login').length,1);
});
test('a bad remote credential cannot destroy the original durable recovery key',async()=>{
 const f=await fixture({lost:true});await f.client.prepareLogin(f.provider);await assert.rejects(f.client.login());f.client.disconnect();
 const publicKey=[...f.rows.values()][0].value.publicKey;f.corruptStatus(true);
 const restored=f.make();assert.equal(await restored.resumeLogin(),false);assert.equal(restored.stage,'session_recovery');
 assert.equal(restored.clientSession.closed,true);assert.equal(f.rows.size,1);assert.equal([...f.rows.values()][0].value.publicKey,publicKey);
 f.corruptStatus(false);assert.equal(await restored.reconcileLogin(),true);assert.equal(restored.expiresAt,1900);
 assert.equal(f.calls.filter(c=>c==='open_session_login').length,1);
});
test('a replaced storage lease prevents an old tab reading or reviving its session',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();
 const slot=[...f.rows.keys()][0];await f.store.reserve(slot,'8'.repeat(32));
 await assert.rejects(f.client.read('/todos/v2'),/browser_session_changed/);
 assert.notEqual(f.client.stage,'ready');assert.equal(f.rows.get(slot).epoch,'8'.repeat(32));
});
test('hard expiry after reload removes child custody and never extends the session',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();f.client.disconnect();f.now(1900);
 const restored=f.make();assert.equal(await restored.resumeLogin(),false);assert.equal(f.rows.size,0);
 assert.equal(f.calls.includes('issuer_status'),false);assert.equal(f.calls.filter(c=>c==='open_session_login').length,1);
});
test('logout storage failure is reported and a retry can still clear the original lease',async()=>{
 const f=await fixture();await f.client.prepareLogin(f.provider);await f.client.login();const remove=f.store.remove;
 f.store.remove=async()=>{throw Error('session_storage_unavailable');};await assert.rejects(f.client.logout(),/session_storage_unavailable/);
 assert.notEqual(f.client.stage,'ready');assert.equal(f.rows.size,1);
 f.store.remove=remove;await f.client.logout();assert.equal(f.rows.size,0);
});
test('Wallet receives the verified service lifetime ceiling and returns the actual consented expiry',async()=>{
 const f=await fixture({maximum:3600});await f.client.prepareLogin(f.provider);
 assert.equal(f.client.loginRequest.requested_ttl_seconds,3600);await f.client.login();
 assert.equal(f.client.expiresAt,1900,'actual fifteen-minute challenge remains authoritative');
 assert.equal([...f.rows.values()][0].value.ttlSeconds,3600);
});
for(const requirement of ['optional','required',undefined])test('grant acquisition preserves membership admission '+String(requirement),async()=>{
 const f=await fixture({acquisition:true,membership:requirement});await f.client.prepareLogin(f.provider);
 assert.equal(f.client.loginRequest.grant_acquisition.membership_requirement,requirement??'required');
 assert.equal(f.client.loginRequest.access_request.request_schema,'devgraph.access-request-action.v1');
 assert.equal(f.client.loginRequest.access_request.result_schemas.length,4);
 assert.equal(f.calls.length,0,'preparation never enrolls, saves or opens a Wallet');
});
test('invalid membership admission cannot become optional proof-free enrollment',async()=>{
 const f=await fixture({acquisition:true,membership:'not-validated'});await assert.rejects(f.client.prepareLogin(f.provider),/wallet_read_setup_required/);
 assert.equal(f.calls.length,0);assert.equal(f.rows.size,0);
});
