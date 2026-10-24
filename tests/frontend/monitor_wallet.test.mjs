import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WalletReadClient} from '../../src/devgraph/frontend/static/credential-v2/read-access.mjs';

function fixture(){
 const events=[],origin='https://graph.example',configuration={schema:'devgraph.wallet-read-configuration.v1',endpoint:'/monitor/wallet-access/v1',web_wallet_url:null,provider_profile:{schema:'castalia.provider-profile.v1',display_name:'Devgraph read access',origins:[origin],membership:null,presentations:[{issuer:'fixture',key_id:'test',public_key:'a'.repeat(64),audience:'devgraph',callers:[{kind:'browser',id:origin}]}]}};
 const provider={getCapabilities:async()=>['read_access_v1','provider_profiles_v1','connection_v1'],proposeProviderProfile:async()=>({state:'approved'}),requestConnection:async()=>({state:'connected'}),readAccess:async input=>{events.push(input.operation);if(input.operation==='identity')return{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32),memberships:[{profileId:'one'}]};if(input.operation==='approve')return{accessId:'access-one',enrollment:{challenge:{},proof:{},signature:'test'}};if(input.operation==='accept')return{accessId:'access-one',expires_at:4000,receiver:origin};if(input.operation==='present')return{credential:{},call:input.call,proof:{}};return{state:'disconnected'}}};
 const fetchImpl=async(url,options={})=>{
  assert.ok(url.startsWith('/monitor/wallet-access/v1'));assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
  const value=url.endsWith('/challenges')?{receiver:origin,expires_at:1120}:url.endsWith('/enrollments')?{schema:'devgraph.native-read-result.v3',credential:{}}:url.endsWith('/requests')?{items:[]} :configuration;
  return{ok:true,status:200,json:async()=>value};
 };
 const client=new WalletReadClient({origin,fetchImpl,now:()=>1000});return{client,provider,events};
}
test('read connection requires provider, site and read approvals before loading graph',async()=>{
 const {client,provider,events}=fixture();await client.initialize();await client.useProvider(provider);
 assert.equal(client.stage,'profile');await assert.rejects(client.read('/monitor/topology/v1'));
 await client.advance();assert.equal(client.stage,'connection');await client.advance();assert.equal(client.stage,'approval');
 await client.advance();assert.equal(client.stage,'ready');assert.ok(client.sessionKey);assert.equal(events.filter(x=>x==='approve').length,1);
 await client.read('/monitor/topology/v1');assert.equal(events.at(-1),'present');
 const profile=client.preferenceSeed;client.disconnect();assert.equal(client.sessionKey,'');assert.ok(profile);await assert.rejects(client.read('/monitor/topology/v1'));
});
test('returning connected Wallet skips unchanged server review without issuing or approving a credential',async()=>{
 const {client,provider,events}=fixture();const read=provider.readAccess;
 provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','read_setup_status_v1'];
 provider.readAccess=async input=>input.operation==='setup_status'?{schema:'castalia.read-setup-status.v1',connection_approved:true,profile_approved:true}:read(input);
 provider.proposeProviderProfile=()=>{throw Error('repeated profile consent');};provider.requestConnection=()=>{throw Error('repeated site consent');};
 await client.initialize();await client.useProvider(provider);assert.equal(client.stage,'approval');
 assert.deepEqual(events,['identity']);assert.equal(client.sessionKey,'');
 await client.advance();assert.equal(client.stage,'ready');assert.equal(events.filter(x=>x==='approve').length,1);
});
test('Wallet setup discovery connects the site before querying saved trust and still reviews changed settings',async()=>{
 for(const profileApproved of [true,false]){
  const {client,provider}=fixture();const read=provider.readAccess;let connected=false,reviews=0;
  provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','read_setup_status_v1'];
  provider.readAccess=async input=>input.operation==='setup_status'?{schema:'castalia.read-setup-status.v1',connection_approved:connected,profile_approved:connected&&profileApproved}:read(input);
  provider.requestConnection=async()=>{connected=true;return{state:'connected'};};provider.proposeProviderProfile=async()=>{reviews++;return{state:'approved'};};
  await client.initialize();await client.useProvider(provider);assert.equal(client.stage,'connection');await client.advance();
  assert.equal(client.stage,profileApproved?'approval':'profile');assert.equal(reviews,0);
  if(!profileApproved){await client.advance();assert.equal(reviews,1);assert.equal(client.stage,'approval');}
 }
});
test('malformed or stale Wallet setup discovery cannot skip server review',async()=>{
 const {client,provider}=fixture();provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','read_setup_status_v1'];
 provider.readAccess=async()=>({schema:'castalia.read-setup-status.v1',connection_approved:false,profile_approved:true});
 await client.initialize();await assert.rejects(client.useProvider(provider),/wallet_setup_status_invalid/);assert.equal(client.identity,null);
});
test('scope does not become arbitrary read or write transport',async()=>{
 const {client,provider,events}=fixture();await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();await client.advance();
 for(const path of ['/work-operations/v1','https://evil.example/','/local/config','/todos/v2/Task/one/../../other'])await assert.rejects(client.read(path));
 assert.equal(events.filter(x=>x==='present').length,0);
});
test('unsupported installed Wallet cannot fall back to pasted credentials',async()=>{
 const {client,provider}=fixture();provider.getCapabilities=async()=>['credential_presentation_v2'];await client.initialize();await assert.rejects(client.useProvider(provider),/wallet_upgrade_required/);
});


test('an unassigned Wallet exposes only public setup details and retries without new site consent',async()=>{
 const {client,provider,events}=fixture();let connections=0,denied=true;
 provider.requestConnection=async()=>{connections++;return{state:'connected'};};
 const fetchImpl=client.fetchImpl;
 client.fetchImpl=async(url,options)=>url.endsWith('/challenges')&&denied?{ok:false,status:401,json:async()=>({error:'member_access_not_granted'})}:fetchImpl(url,options);
 await client.initialize();await client.useProvider(provider);await client.advance();
 await assert.rejects(client.advance(),/wallet_read_denied/);
 assert.equal(client.stage,'access');assert.equal(connections,1);
 assert.deepEqual(client.connectionDetails,{schema:'devgraph.wallet-connection-details.v1',server_origin:'https://graph.example',subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)});
 const copy=client.connectionDetails;copy.subject_public_key='changed';
 assert.equal(client.connectionDetails.subject_public_key,'b'.repeat(64));
 denied=false;await client.advance();assert.equal(client.stage,'approval');assert.equal(connections,1);
 assert.deepEqual(events,['identity']);await client.advance();assert.equal(client.stage,'ready');
 client.disconnect();assert.equal(client.connectionDetails,null);
});

test('an expired read challenge refreshes before a new explicit signing gesture',async()=>{
 const {client,provider,events}=fixture();let now=1000,challenges=0;
 client.now=()=>now;const fetchImpl=client.fetchImpl;
 client.fetchImpl=async(url,options)=>{if(url.endsWith('/challenges')){challenges++;return{ok:true,status:200,json:async()=>({receiver:'https://graph.example',expires_at:now+120})};}return fetchImpl(url,options);};
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();
 now=1120;await client.advance();assert.equal(client.stage,'approval');assert.equal(challenges,2);
 assert.equal(events.filter(x=>x==='approve').length,0);
 await client.advance();assert.equal(client.stage,'ready');assert.equal(events.filter(x=>x==='approve').length,1);
});

test('disconnect while checking access cannot revive public identity or approval',async()=>{
 const {client,provider}=fixture();let release;const fetchImpl=client.fetchImpl;
 client.fetchImpl=async(url,options)=>url.endsWith('/challenges')?new Promise(resolve=>{release=resolve;}):fetchImpl(url,options);
 await client.initialize();await client.useProvider(provider);await client.advance();
 const checking=client.advance();await new Promise(resolve=>setImmediate(resolve));assert.equal(client.stage,'access');
 client.disconnect();release({ok:true,status:200,json:async()=>({receiver:'https://graph.example',expires_at:1120})});
 await assert.rejects(checking,/wallet_connection_changed/);assert.equal(client.stage,'choose');assert.equal(client.connectionDetails,null);
});

test('read approval exposes opening, issuance and accepted states without announcing connection early',async()=>{
 const {client,provider}=fixture();const phases=[];client.onChange=()=>phases.push({stage:client.stage,phase:client.readPhase});
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();
 let approve;const read=provider.readAccess;
 provider.readAccess=input=>input.operation==='approve'?new Promise(resolve=>{approve=resolve;}):read(input);
 const pending=client.advance();assert.equal(client.stage,'approval');assert.equal(client.readPhase,'opening_wallet');assert.equal(client.sessionKey,'');
 approve({accessId:'access-one',enrollment:{challenge:{},proof:{},signature:'test'}});await pending;
 assert.ok(phases.some(x=>x.phase==='issuing_credential'&&x.stage!=='ready'));
 assert.ok(phases.some(x=>x.phase==='accepting_credential'&&x.stage!=='ready'));
 assert.equal(client.stage,'ready');assert.equal(client.readPhase,'');
});
test('Wallet preflight failure clears opening state and sends no enrollment',async()=>{
 const {client,provider}=fixture();await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();
 let enrollments=0;const fetchImpl=client.fetchImpl;client.fetchImpl=async(path,o)=>{if(path.endsWith('/enrollments'))enrollments++;return fetchImpl(path,o);};
 const read=provider.readAccess;provider.readAccess=input=>input.operation==='approve'?Promise.reject(Error('wallet_locked')):read(input);
 await assert.rejects(client.advance(),/wallet_locked/);assert.equal(client.readPhase,'');assert.equal(client.stage,'approval');assert.equal(client.sessionKey,'');assert.equal(enrollments,0);
});
test('read expiry is visible before clicking and refresh never opens Wallet or issues a credential',async()=>{
 const {client,provider,events}=fixture();let now=1000;client.now=()=>now;
 const fetchImpl=client.fetchImpl;client.fetchImpl=async(path,o)=>path.endsWith('/challenges')?{ok:true,status:200,json:async()=>({receiver:'https://graph.example',expires_at:now+120})}:fetchImpl(path,o);
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();assert.equal(client.approvalExpired,false);
 now=1115;assert.equal(client.approvalExpired,true);await client.advance();assert.equal(client.approvalExpired,false);assert.equal(client.notice,'read_scope_refreshed');assert.equal(events.filter(x=>x==='approve').length,0);assert.equal(client.sessionKey,'');
});

for(const [origin,hint] of [['https://pilot.ngrok-free.app',true],['https://graph.example',false],['https://ngrok-free.app.attacker.example',false]])test(`public tunnel metadata remains cookie-free for ${origin}`,async()=>{
 const client=new WalletReadClient({origin,fetchImpl:async(path,options)=>{
  assert.equal(path,'/monitor/wallet-access/v1');assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
  assert.equal(options.headers['ngrok-skip-browser-warning'],hint?'true':undefined);
  return{ok:true,status:200,json:async()=>({schema:'devgraph.wallet-read-configuration.v1',endpoint:'/monitor/wallet-access/v1',web_wallet_url:null})};
 }});
 await client.initialize();assert.equal(client.stage,'choose');
});

test('advertised graph v2 uses its separate read API without community membership or metadata leakage',async()=>{
 const {client,provider,events}=fixture();
 provider.getCapabilities=async()=>['provider_profiles_v1','connection_v1','access_request_v1','graph_access_v2'];
 const old=provider.readAccess,network=client.fetchImpl;
 provider.readAccess=async input=>{
  if(input.operation==='identity')return{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)};
  if(input.operation==='request'){assert.equal(input.profileId,undefined);return{approval:{challenge:input.challenge,signature:'x'.repeat(86),proof:{}}};}
  assert.notEqual(input.operation,'approve');assert.notEqual(input.operation,'accept');assert.notEqual(input.operation,'present');
  if(input.operation.endsWith('_graph_v2')){events.push(input.operation);return old({...input,operation:input.operation.replace('_graph_v2','')});}return old(input);
 };
 client.fetchImpl=async(path,o)=>{if(path.endsWith('/challenges'))assert.deepEqual(Object.keys(JSON.parse(o.body)).sort(),['installation_id','subject_public_key']);return network(path,o);};
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();await client.advance();assert.equal(client.stage,'ready');
 await client.read('/monitor/topology/v1');assert.ok(events.includes('approve_graph_v2'));assert.ok(events.includes('accept_graph_v2'));assert.ok(events.includes('present_graph_v2'));
 const challenge={receiver:'https://graph.example',subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32),details:{schema:'devgraph.access-request-action.v2'}};
 assert.deepEqual((await client.approveAccess(challenge)).proof,{});client.disconnect();assert.equal(client.graphAccessV2,false);
});


test('endpoint setup failure is distinguished from provider or website configuration',async()=>{
 const {client}=fixture();client.fetchImpl=async()=>({ok:false,status:503,json:async()=>({error:'endpoint_wallet_setup_required'})});
 await assert.rejects(client.initialize(),{code:'endpoint_wallet_setup_required',status:503});
 assert.equal(client.configuration,null);assert.equal(client.stage,'unavailable');
 // Only the exact setup response at the public configuration route gets this code.
 await assert.rejects(client.fetch('/monitor/wallet-access/v1/challenges',{body:{}}),{code:'read_unavailable'});
});

test('saved reusable grant connects without enrollment or provider reissuance',async()=>{
 const {client,provider,events}=fixture();const old=client.fetchImpl,read=provider.readAccess;
 client.fetchImpl=async(path,options)=>{assert.ok(!path.endsWith('/enrollments')&&!path.endsWith('/grants'));const r=await old(path,options),v=await r.json();if(path==='/monitor/wallet-access/v1')v.holder_grants={schema:'secs.holder-grant.v1',service:'service:graph',endpoint:'/monitor/wallet-access/v1/grants'};return {...r,json:async()=>v}};
 provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','holder_grant_v1'];
 provider.readAccess=async input=>{if(input.operation==='use_grant'){events.push('use_grant');return{accessId:'saved-grant',service:'service:graph',expires_at:4000}}if(input.operation==='present_grant'){events.push('present_grant');return{credential:{},call:input.call,proof:{}}}return read(input)};
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();assert.equal(client.stage,'saved_grant');await client.advance();assert.equal(client.stage,'ready');await client.read('/monitor/topology/v1');assert.equal(events.at(-1),'present_grant');assert.equal(events.includes('approve'),false);
});
test('first grant is separately reviewed and saved, without legacy credential issuance',async()=>{
 const {client,provider,events}=fixture();const old=client.fetchImpl,read=provider.readAccess;let issued=0;
 client.fetchImpl=async(path,options)=>{assert.ok(!path.endsWith('/enrollments'));if(path.endsWith('/grants')){issued++;return{ok:true,status:200,json:async()=>({schema:'devgraph.holder-grant-result.v1',grant:{claims:{service:'service:graph'}}})}}const r=await old(path,options),v=await r.json();if(path==='/monitor/wallet-access/v1')v.holder_grants={schema:'secs.holder-grant.v1',service:'service:graph',endpoint:'/monitor/wallet-access/v1/grants'};return{...r,json:async()=>v}};
 provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','holder_grant_v1'];
 provider.readAccess=async input=>{if(input.operation==='use_grant')throw Error('grant_not_saved');if(input.operation==='save_grant'){events.push('save_grant');return{accessId:'new-grant',service:'service:graph',expires_at:4000}}return read(input)};
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();await client.advance();assert.equal(client.stage,'approval');await client.advance();assert.equal(client.stage,'grant');assert.equal(client.sessionKey,'');await client.advance();assert.equal(client.stage,'ready');assert.equal(issued,1);assert.equal(events.at(-1),'save_grant');
});

test('lost grant response retains the original signed request and never reopens approval',async()=>{
 const {client,provider,events}=fixture();const old=client.fetchImpl,read=provider.readAccess;const originals=[];
 client.fetchImpl=async(path,options)=>{
  assert.ok(!path.endsWith('/enrollments'));
  if(path.endsWith('/grants')){originals.push(options.body);if(originals.length===1)throw Error('lost response');return{ok:true,status:200,json:async()=>({schema:'devgraph.holder-grant-result.v1',grant:{claims:{service:'service:graph'}}})};}
  const r=await old(path,options),v=await r.json();if(path==='/monitor/wallet-access/v1')v.holder_grants={schema:'secs.holder-grant.v1',service:'service:graph',endpoint:'/monitor/wallet-access/v1/grants'};return{...r,json:async()=>v};
 };
 provider.getCapabilities=async()=>['read_access_v1','provider_profiles_v1','connection_v1','holder_grant_v1'];
 provider.readAccess=async input=>{if(input.operation==='use_grant')throw Error('grant_not_saved');return read(input)};
 await client.initialize();await client.useProvider(provider);await client.advance();await client.advance();await client.advance();
 await assert.rejects(client.advance(),/holder_grant_outcome_unknown/);assert.equal(client.stage,'grant_recovery');
 await client.advance();assert.equal(client.stage,'grant');assert.equal(originals.length,2);assert.equal(originals[0],originals[1]);assert.equal(events.filter(x=>x==='approve').length,1);
});
