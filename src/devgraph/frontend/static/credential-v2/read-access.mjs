import {verifyServiceRoute} from './service-route.mjs';
import {prepareProviderSetup} from './provider-setup.mjs';
import {createBrowserHolderSession} from './holder-session.mjs';
const BASE='/monitor/wallet-access/v1';
const SESSION_BASE='/holder-sessions/v1';
const failure=(code,status=0)=>Object.assign(new Error(code),{code,status});
const plain=v=>!!v&&Object.getPrototypeOf(v)===Object.prototype;
const aborted=signal=>{if(signal?.aborted)throw signal.reason??new DOMException('Aborted','AbortError');};
async function cancellable(pending,signal){
 aborted(signal);if(!signal)return pending;
 let cancel;try{return await Promise.race([pending,new Promise((_,reject)=>{cancel=()=>reject(signal.reason??new DOMException('Aborted','AbortError'));signal.addEventListener('abort',cancel,{once:true});})]);}
 finally{signal.removeEventListener('abort',cancel);}
}
export function monitorReadOperation(target){
 if(typeof target!=='string'||target.length>4096||target.includes('#')||target.split('?')[0].includes('%'))throw failure('read_not_granted',403);
 const path=target.split('?')[0];
 if(path==='/arenas')return'devgraph.arena.list';
 if(/^\/arenas\/[a-z0-9][a-z0-9-]{0,255}(?:\/members)?$/.test(path))return'devgraph.arena.get';
 if(['/monitor/topology/v1','/monitor/snapshot','/monitor/kanban/v2','/workflows/v1','/todos/v2','/todos/v2/classification-report'].includes(path))return'devgraph.work.list';
 if(/^\/todos\/v2\/(Todo|Proposal|Initiative|Project|Issue|Task)\/[a-z0-9][a-z0-9-]{0,255}$/.test(path))return'devgraph.work.get';
 if(/^\/work\/(Todo|Proposal|Initiative|Project|Issue|Task)\/[a-z0-9][a-z0-9-]{0,255}\/relationships\/(children|parent|dependencies|dependents|blockers|blocked)$/.test(path))return'devgraph.work.relationships';
 throw failure('read_not_granted',403);
}
/** Application-owned transport. Wallet holds custody and approved read connections. */
export class WalletReadClient{
 constructor({origin=globalThis.location?.origin,fetchImpl=globalThis.fetch,now=()=>Math.floor(Date.now()/1000),onChange=()=>{}}={}){
  Object.assign(this,{origin,fetchImpl,now,onChange,stage:'unavailable',provider:null,configuration:null,sessionKey:'',preferenceSeed:'',generation:0,accessId:null,memberships:[],profileId:undefined,readPhase:'',usingClientSession:false,clientSession:null,sessionPreparation:null});
 }
 get approvalExpired(){return this.stage==='approval'&&Number.isSafeInteger(this.challenge?.expires_at)&&this.now()>=this.challenge.expires_at-5;}
 expireIfNeeded(){
  const expiry=this.clientSession?.expiresAt??(this.stage==='ready'?this.expiresAt:null);
  if(expiry===null||expiry===undefined||this.now()<expiry&&(!this.usingClientSession||this.clientSession?.active))return false;
  this.disconnect('read_access_expired');return true;
 }
 notify(){this.onChange(this);}
 async fetch(path,{body,signal}={}){
  const headers=body===undefined?{}:{'Content-Type':'application/json'};
  // ngrok's browser notice otherwise replaces anonymous JSON with HTML when
  // cookies are omitted. This public transport hint conveys no authority.
  try{const endpoint=new URL(this.origin);if(endpoint.protocol==='https:'&&endpoint.hostname.endsWith('.ngrok-free.app'))headers['ngrok-skip-browser-warning']='true';}catch{}
  const fetchImpl=this.fetchImpl;const response=await fetchImpl((this.serviceBinding?.claims.prefix??'')+path,{method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body),headers,credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal});
  let data;try{data=await response.json();}catch{throw failure('read_response_invalid',response.status);}
  if(response.status===503&&path===BASE&&body===undefined&&data?.error==='endpoint_wallet_setup_required')throw failure('endpoint_wallet_setup_required',503);
  if(!response.ok)throw failure(response.status===401?'wallet_read_denied':response.status===403?'read_not_granted':response.status===409?'view_changed':'read_unavailable',response.status);
  return data;
 }
 async initialize({signal}={}){
  const config=await this.fetch(BASE,{signal});
  if(!plain(config)||config.schema!=='devgraph.wallet-read-configuration.v1'||config.endpoint!==BASE)throw failure('wallet_read_setup_required');
  if(config.web_wallet_url!==null&&config.web_wallet_url!==undefined){const u=new URL(config.web_wallet_url);if(u.protocol!=='https:'||u.username||u.password||u.hash||u.search)throw failure('wallet_read_setup_required');}
  if(config.holder_grants!==undefined){const h=config.holder_grants;if(!plain(h)||Object.keys(h).sort().join(',')!=='endpoint,schema,service'||!['secs.holder-grant.v1','secs.holder-grant.v2'].includes(h.schema)||typeof h.service!=='string'||!h.service||h.service.length>256||h.endpoint!==BASE+'/grants')throw failure('wallet_read_setup_required');}
  if(config.service_access_point_endpoint!==undefined){
   if(config.service_access_point_endpoint!=='/.well-known/devgraph-service.json'||!config.holder_grants)throw failure('invalid_service_binding');
   const response=await this.fetchImpl(config.service_access_point_endpoint,{credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal});
   if(!response.ok)throw failure('service_binding_unavailable');
   const text=await response.text();if(new TextEncoder().encode(text).length>16384)throw failure('invalid_service_binding');
   this.serviceBinding=await verifyServiceRoute(JSON.parse(text),{origin:this.origin,profile:config.provider_profile,service:config.holder_grants.service,now:this.now()});
  }
  this.authorityOrigin=this.serviceBinding?.claims.authority_receiver??this.origin;
  this.configuration=structuredClone(config);this.stage='choose';this.notify();return config;
 }
 async useProvider(provider){
  this.disconnect();const generation=this.generation;
  const current=()=>{if(this.generation!==generation)throw failure('wallet_connection_changed');};
  if(typeof provider?.readAccess!=='function')throw failure('wallet_upgrade_required');
  if(!this.configuration)await this.initialize();current();
  this.setup=await prepareProviderSetup({provider,origin:this.origin,current,requiredCapability:'read_access_v1',transport:{getProviderProfile:async()=>structuredClone(this.configuration.provider_profile)}});current();
  if(this.serviceBinding&&!this.setup.serviceAccessPoints)throw failure('wallet_upgrade_required');
  this.holderGrants=!!this.configuration.holder_grants&&this.setup.holderGrants===true;if(this.configuration.holder_grants?.schema==='secs.holder-grant.v2'&&!this.setup.holderGrantV2)throw failure('wallet_update_required');this.usingHolderGrant=false;this.supportsRequests=this.setup.accessRequests===true;this.graphAccessV2=this.setup.graphAccessV2===true;this.provider=provider;
  if(this.setup.setupStatus){
   const status=await this.setup.setupStatus();current();this.connectionApproved=status.connection_approved;
   if(this.connectionApproved&&status.profile_approved){await this.identifyAndCheckAccess(current);return;}
   this.stage=this.connectionApproved?'profile':'connection';
  }else this.stage='profile';
  this.notify();
 }
 get connectionDetails(){
  return this.identity?{schema:'devgraph.wallet-connection-details.v1',server_origin:this.origin,subject_public_key:this.identity.subject_public_key,installation_id:this.identity.installation_id}:null;
 }
 async checkAccess(current){
  this.challenge=null;this.stage='access';this.notify();
  const challenge=await this.fetch(BASE+'/challenges',{body:{subject_public_key:this.identity.subject_public_key,installation_id:this.identity.installation_id}});current();
  if(!plain(challenge)||typeof challenge.receiver!=='string'||!Number.isSafeInteger(challenge.expires_at)||challenge.expires_at<=this.now())throw failure('read_challenge_invalid');
  this.challenge=challenge;this.stage='approval';this.notify();
 }
 async identifyAndCheckAccess(current){
  const identity=await this.provider.readAccess({operation:'identity'});current();
  if(!plain(identity)||!/^[a-f0-9]{64}$/.test(identity.subject_public_key)||!/^[a-f0-9]{32}$/.test(identity.installation_id)||(!this.graphAccessV2&&!Array.isArray(identity.memberships)))throw failure('read_identity_invalid');
  this.identity=this.graphAccessV2?{subject_public_key:identity.subject_public_key,installation_id:identity.installation_id}:identity;
  this.memberships=this.graphAccessV2?[]:identity.memberships;this.profileId=this.memberships.length===1?this.memberships[0].profileId:undefined;
  if(this.holderGrants){
   this.stage='saved_grant';this.notify();
   if(this.setup.holderSessions&&this.configuration.holder_grants.schema==='secs.holder-grant.v2'){
    try{await this.prepareClientSession(current);}catch(error){if(!['grant_not_saved','holder_session_parent_unsupported'].includes(error.message))throw error;}
   }
   return;
  }
  await this.checkAccess(current);
 }
 async prepareClientSession(current){
  let caps;
  try{caps=await this.fetch(SESSION_BASE+'/capabilities');current();}catch(error){if(error.status===404)return;throw error;}
  const fields=['schema','supported','profile','issuer_pin','audience','grant_schemas','credential_schema','presentation_schema','read_only','max_ttl_seconds','review_ttl_seconds','proof_max_ttl_seconds','challenge_path','exchange_path','status_path'];
  if(!plain(caps)||Object.keys(caps).sort().join(',')!==fields.sort().join(',')||caps.schema!=='secs.holder-session-capabilities.v1'||typeof caps.supported!=='boolean'||
    caps.profile!=='secs.holder-session.v1'||caps.audience!=='devgraph'||caps.read_only!==true||
    !Array.isArray(caps.grant_schemas)||caps.grant_schemas.length!==1||caps.grant_schemas[0]!=='secs.holder-grant.v2'||
    caps.credential_schema!=='secs.holder-session-credential.v1'||caps.presentation_schema!=='secs.holder-session-presentation.v1'||
    !Number.isSafeInteger(caps.max_ttl_seconds)||caps.max_ttl_seconds<1||caps.max_ttl_seconds>3600||caps.review_ttl_seconds!==120||caps.proof_max_ttl_seconds!==60||
    caps.challenge_path!==(this.serviceBinding?.claims.prefix??'').replace(/\/$/,'')+SESSION_BASE+'/challenges'||caps.exchange_path!==(this.serviceBinding?.claims.prefix??'').replace(/\/$/,'')+SESSION_BASE+'/exchange'||caps.status_path!==(this.serviceBinding?.claims.prefix??'').replace(/\/$/,'')+SESSION_BASE+'/status'||
    !plain(caps.issuer_pin)||Object.keys(caps.issuer_pin).sort().join(',')!=='issuer,key_id,public_key,service'||caps.issuer_pin.service!==this.configuration.holder_grants.service)throw failure('holder_session_setup_invalid');
  const pin=this.setup.profile.presentations.find(p=>p.issuer===caps.issuer_pin.issuer&&p.key_id===caps.issuer_pin.key_id&&p.public_key===caps.issuer_pin.public_key&&p.audience===caps.audience);
  if(!pin)throw failure('holder_session_setup_invalid');
  if(!caps.supported)return;
  const session=await createBrowserHolderSession({origin:this.origin,service:caps.issuer_pin.service,holderPublicKey:this.identity.subject_public_key,
    installationId:this.identity.installation_id,profile:this.setup.profile,ttlSeconds:Math.min(900,caps.max_ttl_seconds),audience:caps.audience,now:this.now});
  try{
   current();
   const prepared=await this.provider.readAccess({operation:'prepare_holder_session',service:caps.issuer_pin.service,session_public_key:session.publicKey,session_installation_id:session.installationId,requested_ttl_seconds:Math.min(900,caps.max_ttl_seconds),...(this.serviceBinding?{service_access_point:this.serviceBinding}:{})});current();
   if(!plain(prepared)||Object.keys(prepared).sort().join(',')!=='challenge,preparationId,schema'||prepared.schema!=='castalia.holder-session-prepared.v1'||typeof prepared.preparationId!=='string'||!prepared.preparationId||prepared.preparationId.length>128)throw failure('invalid_holder_session');
   const readOperations=['devgraph.work.list','devgraph.work.get','devgraph.work.relationships','devgraph.arena.list','devgraph.arena.get'];
   if(!Array.isArray(prepared.challenge?.permissions)||prepared.challenge.permissions.some(p=>!plain(p)||!readOperations.includes(p.operation)||p.resource!=='https://work.zenith-research.ca/devgraph'))throw failure('invalid_holder_session');
   const signature=await session.prepare(prepared.challenge);current();
   this.clientSession=session;this.sessionPreparation={...structuredClone(prepared),session_signature:signature};this.stage='session_review';this.notify();
  }catch(error){session.dispose();throw error;}
 }
 async acceptClientSession(credential,current){
  await this.clientSession.accept(credential);current();
  this.accessId=credential.claims.credential_id;this.expiresAt=this.clientSession.expiresAt;this.usingClientSession=true;
  this.sessionKey=`holder-session:${this.generation}:${this.accessId}`;this.preferenceSeed=`${this.configuration.holder_grants.service}:${this.identity.subject_public_key}`;
  this.stage='ready';this.readPhase='';this.notify();
 }
 async recoverClientSession(current){
  const request=await this.clientSession.status();current();
  const result=await this.fetch(SESSION_BASE+'/status',{body:request});current();
  const c=this.sessionPreparation.challenge;
  if(!plain(result)||Object.keys(result).sort().join(',')!=='challenge_id,credential,expires_at,request_id,schema,state'||result.schema!=='secs.holder-session-result.v1'||result.request_id!==c.request_id||result.challenge_id!==c.challenge_id||result.expires_at!==c.expires_at)throw failure('holder_session_outcome_unknown');
  if(result.state==='issued'&&plain(result.credential)){await this.acceptClientSession(result.credential,current);return;}
  if(result.credential!==null||!['pending','unknown','revoked','expired'].includes(result.state))throw failure('holder_session_outcome_unknown');
  if(['revoked','expired'].includes(result.state)){this.disconnect('read_access_expired');throw failure('read_access_expired');}
  this.notice='holder_session_outcome_unknown';this.notify();
 }
 async advance(){
  this.notice='';
  const generation=this.generation,current=()=>{if(generation!==this.generation)throw failure('wallet_connection_changed');};
  if(this.stage==='session_recovery'){await this.recoverClientSession(current);return;}
  if(this.stage==='session_review'){
   const p=this.sessionPreparation;if(!p||!this.clientSession)throw failure('holder_session_preparation_required');
   this.readPhase='opening_wallet';this.notify();
   try{
    // The possession signature is prepared earlier so this consent starts in the click.
    const pending=this.provider.readAccess({operation:'open_holder_session',preparationId:p.preparationId,challenge:structuredClone(p.challenge),session_signature:p.session_signature});
    const result=await pending;current();
    if(!plain(result)||Object.keys(result).sort().join(',')!=='credential,schema'||result.schema!=='castalia.holder-session-opened.v1'||!plain(result.credential))throw failure('invalid_holder_session');
    this.readPhase='accepting_credential';this.notify();await this.acceptClientSession(result.credential,current);return;
   }catch(error){
    current();this.readPhase='';
    if(this.clientSession?.closed){this.disconnect();throw error;}
    if(['approval_denied','user_denied','read_approval_expired'].includes(error.code??error.message)){this.disconnect();throw error;}
    this.stage='session_recovery';this.notify();throw failure('holder_session_outcome_unknown');
   }
  }
  if(this.stage==='saved_grant'){
   try{const accepted=await this.provider.readAccess({operation:'use_grant',service:this.configuration.holder_grants.service});current();this.acceptHolder(accepted);return;}
   catch(error){if(error?.code!=='grant_not_saved'&&error?.message!=='grant_not_saved')throw error;await this.checkAccess(current);return;}
  }
  if(this.stage==='grant_recovery'){await this.obtainHolderGrant(current);return;}
  if(this.stage==='grant'){
   const accepted=await this.provider.readAccess({operation:'save_grant',service:this.configuration.holder_grants.service,grant:structuredClone(this.pendingGrant)});current();this.acceptHolder(accepted);this.pendingGrant=null;return;
  }
  if(this.stage==='profile'){const pending=this.setup.approve();await pending;current();if(this.connectionApproved){await this.identifyAndCheckAccess(current);return;}this.stage='connection';this.notify();return;}
  if(this.stage==='connection'){
   const pending=this.setup.connect();await pending;current();
   if(this.setup.setupStatus){const status=await this.setup.setupStatus();current();if(!status.connection_approved)throw failure('wallet_connection_changed');this.connectionApproved=true;if(!status.profile_approved){this.stage='profile';this.notify();return;}}
   await this.identifyAndCheckAccess(current);return;
  }
  if(this.stage==='access'){await this.checkAccess(current);return;}
  if(this.stage!=='approval')throw failure('wallet_read_access_required');
  if(!this.graphAccessV2&&this.memberships.length>1&&!this.profileId)throw failure('membership_selection_required');
  if(this.approvalExpired){await this.checkAccess(current);this.notice='read_scope_refreshed';this.notify();return;}
  // Start the consent RPC immediately in this user gesture, before network waits.
  this.readPhase='opening_wallet';this.notify();
  let approved;
  try{const pending=this.provider.readAccess({operation:this.graphAccessV2?'approve_graph_v2':'approve',challenge:this.challenge,...(this.serviceBinding?{service_access_point:this.serviceBinding}:{}),...(this.profileId?{profileId:this.profileId}:{})});approved=await pending;current();}
  catch(error){if(generation===this.generation){this.readPhase='';this.notify();}throw error;}
  if(!plain(approved)||typeof approved.accessId!=='string'||!plain(approved.enrollment)){this.readPhase='';this.notify();throw failure('read_approval_invalid');}
  this.accessId=approved.accessId;
  try{
   this.readPhase='issuing_credential';this.notify();
   if(this.holderGrants){
    this.originalGrantRequest=structuredClone(approved.enrollment);
    await this.obtainHolderGrant(current);return;
   }
   const result=await this.fetch(BASE+'/enrollments',{body:approved.enrollment});current();
   if(!['devgraph.native-read-result.v3','devgraph.native-read-result.v4','devgraph.native-read-result.v5'].includes(result?.schema)||!plain(result.credential))throw failure('read_credential_invalid');
   this.readPhase='accepting_credential';this.notify();
   const accepted=await this.provider.readAccess({operation:this.graphAccessV2?'accept_graph_v2':'accept',accessId:this.accessId,credential:result.credential});current();
   if(accepted?.accessId!==this.accessId||accepted.receiver!==this.challenge.receiver||!Number.isSafeInteger(accepted.expires_at)||accepted.expires_at<=this.now())throw failure('read_credential_invalid');
   this.expiresAt=accepted.expires_at;this.sessionKey=`wallet:${this.generation}:${this.accessId}`;
   this.preferenceSeed=`${this.origin}:${this.identity.subject_public_key}`;this.readPhase='';this.stage='ready';this.notify();
  }catch(error){if(this.stage!=='grant_recovery')this.disconnect();throw error;}
 }
 async obtainHolderGrant(current){
  if(!this.originalGrantRequest)throw failure('original_grant_request_missing');
  this.stage='grant_recovery';this.readPhase='';this.notify();
  try{const result=await this.fetch(BASE+'/grants',{body:structuredClone(this.originalGrantRequest)});current();
   if(result?.schema!=='devgraph.holder-grant-result.v1'||!plain(result.grant)||result.grant.claims?.service!==this.configuration.holder_grants.service)throw failure('invalid_holder_grant');
   this.pendingGrant=structuredClone(result.grant);this.stage='grant';this.notify();
  }catch(error){current();throw failure('holder_grant_outcome_unknown');}
 }
 acceptHolder(accepted){
  if(!plain(accepted)||typeof accepted.accessId!=='string'||accepted.service!==this.configuration.holder_grants.service||!Number.isSafeInteger(accepted.expires_at)||accepted.expires_at<=this.now()||accepted.expires_at-this.now()>3600)throw failure('invalid_holder_credential');
  this.accessId=accepted.accessId;this.expiresAt=accepted.expires_at;this.usingHolderGrant=true;this.readPhase='';this.stage='ready';this.sessionKey=`holder:${this.generation}:${this.accessId}`;this.preferenceSeed=`${this.configuration.holder_grants.service}:${this.identity.subject_public_key}`;this.notify();
 }
 approveAccess(challenge){
  if(!this.supportsRequests||!this.provider?.readAccess)throw failure('wallet_request_upgrade_required');
  if(!this.identity||challenge.subject_public_key!==this.identity.subject_public_key||challenge.installation_id!==this.identity.installation_id||challenge.receiver!==this.authorityOrigin)throw failure('wallet_request_identity_changed');
  const resourceRequest=['devgraph.access-request-action.v1','devgraph.access-request-action.v2'].includes(challenge.details?.schema);
  const requiresMembership=!this.graphAccessV2&&resourceRequest;
  if(requiresMembership&&!this.memberships.length)throw failure('membership_required');
  if(requiresMembership&&this.memberships.length>1&&!this.profileId)throw failure('membership_selection_required');
  const generation=this.generation,expected=structuredClone(challenge);
  const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
  const pending=this.provider.readAccess({operation:'request',challenge:expected,...(this.serviceBinding?{service_access_point:this.serviceBinding}:{}),...(resourceRequest&&this.profileId?{profileId:this.profileId}:{})});
  return pending.then(result=>{
   if(generation!==this.generation)throw failure('wallet_connection_changed');
   const approval=result?.approval;
   if(!plain(approval)||stable(approval.challenge)!==stable(expected)||typeof approval.signature!=='string'||!/^[A-Za-z0-9_-]{86}$/.test(approval.signature)||!plain(approval.proof))throw failure('wallet_request_approval_invalid');
   return approval;
  });
 }
 async read(path,{signal}={}){
  if(this.expireIfNeeded())throw failure('read_access_expired',401);
  if(this.stage!=='ready'||!this.accessId)throw failure('wallet_read_access_required',401);
  const operation=monitorReadOperation(path),generation=this.generation;
  const call={operation,resource:'https://work.zenith-research.ca/devgraph',parameters:{path}};
  try{
   const pending=this.usingClientSession?this.clientSession.present(call):this.provider.readAccess({operation:this.usingHolderGrant?'present_grant':this.graphAccessV2?'present_graph_v2':'present',accessId:this.accessId,call});
   const envelope=await cancellable(pending,signal);aborted(signal);
   if(generation!==this.generation)throw failure('wallet_connection_changed');
   if(!plain(envelope)||!plain(envelope.credential)||!plain(envelope.proof)||!plain(envelope.call)||Object.keys(envelope.call).sort().join(',')!=='operation,parameters,resource'||envelope.call.operation!==call.operation||envelope.call.resource!==call.resource||!plain(envelope.call.parameters)||Object.keys(envelope.call.parameters).join(',')!=='path'||envelope.call.parameters.path!==path)throw failure('read_presentation_invalid');
   const result=await this.fetch(BASE+'/requests',{body:envelope,signal});
   if(generation!==this.generation)throw failure('wallet_connection_changed');return result;
  }catch(error){if(error.status===401||['wallet_locked','read_access_required','read_context_changed','read_access_expired','holder_session_expired','holder_session_closed','holder_session_clock_changed'].includes(error.message))this.disconnect();throw error;}
 }
 disconnect(notice=''){
  const provider=this.provider,accessId=this.usingClientSession?null:this.accessId;this.provider=null;
  this.clientSession?.dispose();this.clientSession=null;this.sessionPreparation=null;this.usingClientSession=false;
  this.generation++;this.pendingGrant=null;this.originalGrantRequest=null;this.usingHolderGrant=false;this.accessId=null;this.expiresAt=null;this.sessionKey='';this.preferenceSeed='';this.identity=null;this.challenge=null;this.memberships=[];this.graphAccessV2=false;this.notice=notice;this.connectionApproved=false;
  this.readPhase='';this.stage=this.configuration?'choose':'unavailable';this.notify();
  if(accessId&&provider?.readAccess)void provider.readAccess({operation:'disconnect',accessId}).catch(()=>{});
  provider?.dispose?.();
 }
}
