import {prepareProviderSetup} from './provider-setup.mjs';
const BASE='/monitor/wallet-access/v1';
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
  Object.assign(this,{origin,fetchImpl,now,onChange,stage:'unavailable',provider:null,configuration:null,sessionKey:'',preferenceSeed:'',generation:0,accessId:null,memberships:[],profileId:undefined,readPhase:''});
 }
 get approvalExpired(){return this.stage==='approval'&&Number.isSafeInteger(this.challenge?.expires_at)&&this.now()>=this.challenge.expires_at-5;}
 notify(){this.onChange(this);}
 async fetch(path,{body,signal}={}){
  const headers=body===undefined?{}:{'Content-Type':'application/json'};
  // ngrok's browser notice otherwise replaces anonymous JSON with HTML when
  // cookies are omitted. This public transport hint conveys no authority.
  try{const endpoint=new URL(this.origin);if(endpoint.protocol==='https:'&&endpoint.hostname.endsWith('.ngrok-free.app'))headers['ngrok-skip-browser-warning']='true';}catch{}
  const fetchImpl=this.fetchImpl;const response=await fetchImpl(path,{method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body),headers,credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal});
  let data;try{data=await response.json();}catch{throw failure('read_response_invalid',response.status);}
  if(response.status===503&&path===BASE&&body===undefined&&data?.error==='endpoint_wallet_setup_required')throw failure('endpoint_wallet_setup_required',503);
  if(!response.ok)throw failure(response.status===401?'wallet_read_denied':response.status===403?'read_not_granted':response.status===409?'view_changed':'read_unavailable',response.status);
  return data;
 }
 async initialize({signal}={}){
  const config=await this.fetch(BASE,{signal});
  if(!plain(config)||config.schema!=='devgraph.wallet-read-configuration.v1'||config.endpoint!==BASE)throw failure('wallet_read_setup_required');
  if(config.web_wallet_url!==null&&config.web_wallet_url!==undefined){const u=new URL(config.web_wallet_url);if(u.protocol!=='https:'||u.username||u.password||u.hash||u.search)throw failure('wallet_read_setup_required');}
  if(config.holder_grants!==undefined){const h=config.holder_grants;if(!plain(h)||Object.keys(h).sort().join(',')!=='endpoint,schema,service'||h.schema!=='secs.holder-grant.v1'||typeof h.service!=='string'||!h.service||h.service.length>256||h.endpoint!==BASE+'/grants')throw failure('wallet_read_setup_required');}
  this.configuration=structuredClone(config);this.stage='choose';this.notify();return config;
 }
 async useProvider(provider){
  this.disconnect();const generation=this.generation;
  const current=()=>{if(this.generation!==generation)throw failure('wallet_connection_changed');};
  if(typeof provider?.readAccess!=='function')throw failure('wallet_upgrade_required');
  if(!this.configuration)await this.initialize();current();
  this.setup=await prepareProviderSetup({provider,origin:this.origin,current,requiredCapability:'read_access_v1',transport:{getProviderProfile:async()=>structuredClone(this.configuration.provider_profile)}});current();
  this.holderGrants=!!this.configuration.holder_grants&&this.setup.holderGrants===true;this.usingHolderGrant=false;this.supportsRequests=this.setup.accessRequests===true;this.graphAccessV2=this.setup.graphAccessV2===true;this.provider=provider;
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
  if(this.holderGrants){this.stage='saved_grant';this.notify();return;}
  await this.checkAccess(current);
 }
 async advance(){
  this.notice='';
  const generation=this.generation,current=()=>{if(generation!==this.generation)throw failure('wallet_connection_changed');};
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
  try{const pending=this.provider.readAccess({operation:this.graphAccessV2?'approve_graph_v2':'approve',challenge:this.challenge,...(this.profileId?{profileId:this.profileId}:{})});approved=await pending;current();}
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
  if(!this.identity||challenge.subject_public_key!==this.identity.subject_public_key||challenge.installation_id!==this.identity.installation_id||challenge.receiver!==this.origin)throw failure('wallet_request_identity_changed');
  const resourceRequest=['devgraph.access-request-action.v1','devgraph.access-request-action.v2'].includes(challenge.details?.schema);
  const requiresMembership=!this.graphAccessV2&&resourceRequest;
  if(requiresMembership&&!this.memberships.length)throw failure('membership_required');
  if(requiresMembership&&this.memberships.length>1&&!this.profileId)throw failure('membership_selection_required');
  const generation=this.generation,expected=structuredClone(challenge);
  const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
  const pending=this.provider.readAccess({operation:'request',challenge:expected,...(resourceRequest&&this.profileId?{profileId:this.profileId}:{})});
  return pending.then(result=>{
   if(generation!==this.generation)throw failure('wallet_connection_changed');
   const approval=result?.approval;
   if(!plain(approval)||stable(approval.challenge)!==stable(expected)||typeof approval.signature!=='string'||!/^[A-Za-z0-9_-]{86}$/.test(approval.signature)||!plain(approval.proof))throw failure('wallet_request_approval_invalid');
   return approval;
  });
 }
 async read(path,{signal}={}){
  if(this.stage!=='ready'||!this.accessId)throw failure('wallet_read_access_required',401);
  if(this.now()>=this.expiresAt){this.disconnect();throw failure('read_access_expired',401);}
  const operation=monitorReadOperation(path),generation=this.generation;
  const call={operation,resource:'https://work.zenith-research.ca/devgraph',parameters:{path}};
  try{
   const envelope=await cancellable(this.provider.readAccess({operation:this.usingHolderGrant?'present_grant':this.graphAccessV2?'present_graph_v2':'present',accessId:this.accessId,call}),signal);aborted(signal);
   if(generation!==this.generation)throw failure('wallet_connection_changed');
   if(!plain(envelope)||!plain(envelope.credential)||!plain(envelope.proof)||!plain(envelope.call)||Object.keys(envelope.call).sort().join(',')!=='operation,parameters,resource'||envelope.call.operation!==call.operation||envelope.call.resource!==call.resource||!plain(envelope.call.parameters)||Object.keys(envelope.call.parameters).join(',')!=='path'||envelope.call.parameters.path!==path)throw failure('read_presentation_invalid');
   const result=await this.fetch(BASE+'/requests',{body:envelope,signal});
   if(generation!==this.generation)throw failure('wallet_connection_changed');return result;
  }catch(error){if(error.status===401||['wallet_locked','read_access_required','read_context_changed','read_access_expired'].includes(error.message))this.disconnect();throw error;}
 }
 disconnect(){
  const provider=this.provider,accessId=this.accessId;this.provider=null;
  this.generation++;this.pendingGrant=null;this.originalGrantRequest=null;this.usingHolderGrant=false;this.accessId=null;this.sessionKey='';this.preferenceSeed='';this.identity=null;this.challenge=null;this.memberships=[];this.graphAccessV2=false;this.notice='';this.connectionApproved=false;
  this.readPhase='';this.stage=this.configuration?'choose':'unavailable';this.notify();
  if(accessId&&provider?.readAccess)void provider.readAccess({operation:'disconnect',accessId}).catch(()=>{});
  provider?.dispose?.();
 }
}
