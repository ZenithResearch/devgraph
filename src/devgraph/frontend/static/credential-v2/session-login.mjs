import {WalletReadClient} from './read-access.mjs';
import {canonicalSessionJson,createBrowserLoginSession,restoreBrowserHolderSession} from './holder-session.mjs';
import {BrowserSessionStore} from './session-store.mjs';

const BASE='/holder-sessions/v1';
const plain=value=>!!value&&Object.getPrototypeOf(value)===Object.prototype;
const shape=(value,fields)=>plain(value)&&Object.keys(value).sort().join(',')===[...fields].sort().join(',');
const error=code=>Object.assign(Error(code),{code});
const hex=value=>Array.from(value,b=>b.toString(16).padStart(2,'0')).join('');
const READS=['devgraph.work.list','devgraph.work.get','devgraph.work.relationships','devgraph.arena.list','devgraph.arena.get'];

/** One page action, one Wallet window, finite browser-owned custody. */
export class WalletLoginClient extends WalletReadClient {
 constructor({store=new BrowserSessionStore(),cryptoImpl=globalThis.crypto,...options}={}){
  super(options);Object.assign(this,{store,cryptoImpl,lease:null,loginId:null,loginRequest:null,loginProvider:null});
 }
 async configureLogin(){
  if(!this.configuration)await this.initialize();
  const service=this.configuration.holder_grants?.service;
  if(!service)throw error('holder_session_unavailable');
  this.slot='devgraph.session.v1:'+this.origin+':'+service;
  return service;
 }
 current(generation){if(this.generation!==generation)throw error('wallet_connection_changed');}
 async prepareLogin(provider){
  const generation=this.generation,service=await this.configureLogin();this.current(generation);
  if(typeof provider?.readAccess!=='function'||typeof provider.getCapabilities!=='function')throw error('wallet_update_required');
  const available=await provider.getCapabilities();this.current(generation);
  if(!Array.isArray(available)||!available.includes('session_login_v1'))throw error('wallet_update_required');
  const c=await this.fetch(BASE+'/capabilities');this.current(generation);
  const fields=['schema','supported','profile','issuer_pin','audience','grant_schemas','credential_schema','presentation_schema','read_only','max_ttl_seconds','review_ttl_seconds','proof_max_ttl_seconds','challenge_path','exchange_path','status_path'];
  const prefix=this.serviceBinding?.claims.prefix??'';
  if(!shape(c,fields)||c.schema!=='secs.holder-session-capabilities.v1'||c.supported!==true||c.profile!=='secs.holder-session.v1'||
   c.audience!=='devgraph'||c.read_only!==true||JSON.stringify(c.grant_schemas)!=='["secs.holder-grant.v2"]'||
   c.credential_schema!=='secs.holder-session-credential.v1'||c.presentation_schema!=='secs.holder-session-presentation.v1'||
   !Number.isSafeInteger(c.max_ttl_seconds)||c.max_ttl_seconds<1||c.max_ttl_seconds>3600||c.review_ttl_seconds!==120||c.proof_max_ttl_seconds!==60||
   c.challenge_path!==prefix+BASE+'/challenges'||c.exchange_path!==prefix+BASE+'/exchange'||c.status_path!==prefix+BASE+'/status'||
   !shape(c.issuer_pin,['issuer','key_id','public_key','service'])||c.issuer_pin.service!==service||
   !this.configuration.provider_profile.presentations.some(p=>p.issuer===c.issuer_pin.issuer&&p.key_id===c.issuer_pin.key_id&&p.public_key===c.issuer_pin.public_key&&p.audience===c.audience))throw error('holder_session_setup_invalid');
  this.clientSession?.dispose();
  // This is the requested upper bound. Wallet offers a default/shorter duration
  // after checking the signed parent and current service policy, then the human
  // approves the exact returned challenge expiry in the popup.
  const ttl=c.max_ttl_seconds;
  this.clientSession=await createBrowserLoginSession({origin:this.origin,service,profile:this.configuration.provider_profile,ttlSeconds:ttl,cryptoImpl:this.cryptoImpl,now:this.now});
  this.current(generation);
  this.loginProvider=provider;this.loginId=hex(this.cryptoImpl.getRandomValues(new Uint8Array(16)));
  this.loginRequest={operation:'open_session_login',loginId:this.loginId,service,session_public_key:this.clientSession.publicKey,
   session_installation_id:this.clientSession.installationId,requested_ttl_seconds:ttl,profile:structuredClone(this.configuration.provider_profile),
   ...(this.serviceBinding?{service_access_point:structuredClone(this.serviceBinding)}:{}),
   ...(this.configuration.holder_grants.v2_endpoint?{grant_acquisition:{schema:'castalia.session-login-grant.v1',challenge_path:'/monitor/wallet-access/v1/challenges',grant_path:this.configuration.holder_grants.v2_endpoint,grant_schema:'secs.holder-grant.v2',result_schema:'devgraph.holder-grant-result.v1'}}:{}),
   ...(this.configuration.access_requests_endpoint==='/devgraph/access-requests/v2'?{access_request:{schema:'castalia.session-login-access.v1',challenge_path:'/devgraph/access-requests/v2/challenges',action_path:'/devgraph/access-requests/v2/actions',...(this.configuration.access_catalog_endpoint?{catalog_path:'/devgraph/access-requests/v2/catalog'}:{})}}:{})};
  this.stage='login_ready';this.notice='';this.notify();
 }
 async cancelPopup(provider,id){if(id&&provider?.readAccess)try{await provider.readAccess({operation:'cancel_session_login',loginId:id});}catch{}}
 async connectedCredential(credential,generation){
  await this.clientSession.accept(credential);this.current(generation);
  await this.store.save(this.slot,this.lease,this.clientSession.checkpoint());this.current(generation);
  this.identity={subject_public_key:credential.claims.challenge.holder_public_key,installation_id:credential.claims.challenge.installation_id};
  this.accessId=credential.claims.credential_id;this.expiresAt=this.clientSession.expiresAt;this.usingClientSession=true;
  this.sessionKey='holder-session:'+this.lease+':'+this.accessId;
  this.preferenceSeed=this.configuration.holder_grants.service+':'+this.identity.subject_public_key;
  this.stage='ready';this.readPhase='';this.notice='';this.notify();
 }
 async login(){
  if(this.stage!=='login_ready'||!this.loginRequest||!this.clientSession)throw error('session_login_busy');
  const generation=this.generation,provider=this.loginProvider,id=this.loginId,session=this.clientSession;
  this.lease=id;this.stage='opening_wallet';this.notice='';this.notify();
  let completionStarted=false,pending;
  try{
   // Open in the original gesture; storage/network waits must not precede it.
   pending=Promise.resolve(provider.readAccess(structuredClone(this.loginRequest)));void pending.catch(()=>{});
   await this.store.reserve(this.slot,id);this.current(generation);
   const prepared=await pending;this.current(generation);
   if(!shape(prepared,['schema','loginId','challenge'])||prepared.schema!=='castalia.session-login-prepared.v1'||prepared.loginId!==id||
    !Array.isArray(prepared.challenge?.permissions)||prepared.challenge.permissions.some(p=>!plain(p)||!READS.includes(p.operation)||p.resource!=='https://work.zenith-research.ca/devgraph'))throw error('invalid_holder_session');
   const signature=await session.prepare(prepared.challenge);this.current(generation);
   this.sessionPreparation={challenge:structuredClone(prepared.challenge),session_signature:signature,preparationId:id};
   // A lost committed reply remains recoverable after reload with this key.
   await this.store.save(this.slot,id,session.checkpoint());this.current(generation);
   this.stage='awaiting_consent';this.notify();completionStarted=true;
   const result=await provider.readAccess({operation:'complete_session_login',loginId:id,challenge:structuredClone(prepared.challenge),session_signature:signature});this.current(generation);
   if(!shape(result,['schema','credential'])||result.schema!=='castalia.holder-session-opened.v1'||!plain(result.credential))throw error('invalid_holder_session');
   await this.connectedCredential(result.credential,generation);
  }catch(cause){
   if(this.generation!==generation){void this.cancelPopup(provider,id);throw error('wallet_connection_changed');}
   if(completionStarted&&cause?.dispatched!==false){
    this.stage='session_recovery';this.notice=cause?.code==='session_storage_unavailable'?'session_storage_unavailable':'holder_session_outcome_unknown';
    this.notify();throw error(this.notice);
   }
   await this.store.remove(this.slot,id).catch(()=>{});session.dispose();this.clientSession=null;this.sessionPreparation=null;
   void this.cancelPopup(provider,id);
   this.stage=['grant_not_saved','session_login_access_pending'].includes(cause?.code??cause?.message)?'needs_access':'signed_out';
   this.notice=cause?.code??cause?.message??'session_login_failed';this.notify();throw cause;
  }
 }
 async restoreCheckpoint(row,generation){
  if(!row||typeof row.epoch!=='string'||!/^[a-f0-9]{32}$/.test(row.epoch)||!plain(row.value))throw error('browser_session_invalid');
  this.lease=row.epoch;
  this.clientSession=await restoreBrowserHolderSession(row.value,{origin:this.origin,service:this.configuration.holder_grants.service,profile:this.configuration.provider_profile,cryptoImpl:this.cryptoImpl,now:this.now});this.current(generation);
  this.sessionPreparation={challenge:structuredClone(row.value.challenge)};
 }
 async resumeLogin(){
  const generation=this.generation;await this.configureLogin();this.current(generation);
  const row=await this.store.load(this.slot);this.current(generation);
  if(!row?.value){this.stage='signed_out';this.notify();return false;}
  try{await this.restoreCheckpoint(row,generation);return await this.reconcileLogin();}
  catch(cause){
   this.current(generation);
   const terminal=['invalid_holder_session','browser_session_invalid','browser_session_changed'].includes(cause?.code??cause?.message);
   if(this.clientSession&&!this.clientSession.closed&&cause?.status!==401&&!terminal){this.stage='session_recovery';this.notice='holder_session_outcome_unknown';this.notify();return false;}
   await this.store.remove(this.slot,row.epoch);this.current(generation);this.clientSession?.dispose();this.clientSession=null;
   this.stage='signed_out';this.notice='read_access_expired';this.notify();return false;
  }
 }
 async reconcileLogin(){
  const generation=this.generation;
  if(!this.clientSession||this.clientSession.closed){await this.restoreCheckpoint(await this.store.load(this.slot),generation);}
  const request=await this.clientSession.status();this.current(generation);
  const result=await this.fetch(BASE+'/status',{body:request});this.current(generation);
  const c=this.sessionPreparation.challenge;
  if(!shape(result,['schema','request_id','challenge_id','state','credential','expires_at'])||result.schema!=='secs.holder-session-result.v1'||result.request_id!==c.request_id||result.challenge_id!==c.challenge_id||result.expires_at!==c.expires_at)throw error('holder_session_outcome_unknown');
  if(result.state==='issued'&&plain(result.credential)){
   // A restored ready checkpoint already verified this token. Re-enter the
   // reviewed state from its stored key before accepting the authoritative copy.
   if(this.clientSession.active){
    if(canonicalSessionJson(this.clientSession.checkpoint().credential)!==canonicalSessionJson(result.credential))throw error('invalid_holder_session');
    // The original token was verified on restore. Status cannot replace it.
    const credential=this.clientSession.checkpoint().credential;
    await this.store.save(this.slot,this.lease,this.clientSession.checkpoint());this.current(generation);
    this.identity={subject_public_key:c.holder_public_key,installation_id:c.installation_id};
    this.accessId=credential.claims.credential_id;this.expiresAt=c.expires_at;this.usingClientSession=true;
    this.sessionKey='holder-session:'+this.lease+':'+this.accessId;
    this.preferenceSeed=this.configuration.holder_grants.service+':'+this.identity.subject_public_key;
    this.stage='ready';this.notice='';this.notify();return true;
   }
   await this.connectedCredential(result.credential,generation);return true;
  }
  if(result.credential!==null||!['pending','unknown','revoked','expired'].includes(result.state))throw error('holder_session_outcome_unknown');
  if(['revoked','expired'].includes(result.state)){await this.logout();this.notice=result.state==='revoked'?'read_access_revoked':'read_access_expired';this.notify();return false;}
  this.stage='session_recovery';this.notice='holder_session_outcome_unknown';this.notify();return false;
 }
 async read(path,options){
  const generation=this.generation,row=await this.store.load(this.slot);this.current(generation);
  if(row?.epoch!==this.lease){this.disconnect();throw error('browser_session_changed');}
  const result=await super.read(path,options);this.current(generation);
  await this.store.save(this.slot,this.lease,this.clientSession.checkpoint());this.current(generation);return result;
 }
 async logout(){
  const provider=this.loginProvider,id=this.loginId,lease=this.lease,slot=this.slot;
  this.disconnect('signed_out');this.loginRequest=null;this.loginId=null;this.loginProvider=null;
  try{if(slot&&lease)await this.store.remove(slot,lease);}
  finally{void this.cancelPopup(provider,id);}
 }
}
