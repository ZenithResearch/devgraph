/** Finite application custody. No Wallet key, reusable grant or storage API. */
const fail=code=>{throw Error(code);};
const invalid=()=>fail('invalid_holder_session');
const plain=value=>value!==null&&typeof value==='object'&&Object.getPrototypeOf(value)===Object.prototype;
const shape=(value,fields)=>plain(value)&&Object.keys(value).sort().join(',')===[...fields].sort().join(',');
const hex=value=>Array.from(new Uint8Array(value),b=>b.toString(16).padStart(2,'0')).join('');
const unhex=value=>Uint8Array.from(value.match(/../g),b=>parseInt(b,16));
const isHex=(value,length)=>typeof value==='string'&&new RegExp(`^[a-f0-9]{${length}}$`).test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
function scalarString(value){
 for(let i=0;i<value.length;i++){
  const code=value.charCodeAt(i);
  if(code>=0xd800&&code<=0xdbff){const next=value.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))invalid();}
  else if(code>=0xdc00&&code<=0xdfff)invalid();
 }
 return JSON.stringify(value);
}
export function canonicalSessionJson(value){
 function encode(v,depth){
  if(depth>32)invalid();
  if(v===null||typeof v==='boolean')return JSON.stringify(v);
  if(typeof v==='string')return scalarString(v);
  if(typeof v==='number'){if(!Number.isSafeInteger(v))invalid();return JSON.stringify(v);}
  if(Array.isArray(v))return '['+Array.from(v,x=>encode(x,depth+1)).join(',')+']';
  if(!plain(v))invalid();
  return '{'+Object.keys(v).sort().map(k=>scalarString(k)+':'+encode(v[k],depth+1)).join(',')+'}';
 }
 const text=encode(value,0);if(new TextEncoder().encode(text).length>131072)invalid();return text;
}
const transcript=(name,value)=>new TextEncoder().encode(name+'\0'+canonicalSessionJson(value));
const challengeFields=['schema','challenge_id','request_id','nonce','issuer','key_id','service','audience','origin','parent_grant_id','parent_grant_digest','parent_revision','holder_public_key','installation_id','session_installation_id','session_public_key','permissions','issued_at','review_deadline','expires_at'];

class BrowserHolderSession {
 #key;#public;#options;#created;#last;#state='created';#challenge=null;#credential=null;#pin=null;#hide;
 constructor(key,publicKey,options,created){this.#key=key;this.#public=publicKey;this.#options=options;this.#created=created;this.#last=created;this.#hide=()=>this.dispose();options.lifecycle?.addEventListener?.('pagehide',this.#hide,{once:true});Object.freeze(this);}
 get publicKey(){return this.#public;}
 get installationId(){return this.#options.sessionInstallationId;}
 get closed(){return this.#state==='closed';}
 get expiresAt(){return this.#challenge?.expires_at??null;}
 get active(){try{return this.#state==='ready'&&this.#time()<this.#challenge.expires_at;}catch{return false;}}
 #time(){
  if(this.#state==='closed')fail('holder_session_closed');
  const now=this.#options.now();
  if(!integer(now)||now<this.#last){this.dispose();fail('holder_session_clock_changed');}
  this.#last=now;
  if(this.#challenge&&now>=this.#challenge.expires_at){this.dispose();fail('holder_session_expired');}
  return now;
 }
 #require(state){this.#time();if(this.#state!==state)fail('holder_session_state');}
 async #digest(value){return hex(await this.#options.cryptoImpl.subtle.digest('SHA-256',new TextEncoder().encode(canonicalSessionJson(value))));}
 async #sign(name,value){return hex(await this.#options.cryptoImpl.subtle.sign('Ed25519',this.#key,transcript(name,value)));}
 #admitChallenge(value,resuming=false){
  const c=structuredClone(value),o=this.#options,now=this.#time();
  canonicalSessionJson(c);
  if(!shape(c,challengeFields)||c.schema!=='secs.holder-session-challenge.v1'||
    !isHex(c.challenge_id,32)||!isHex(c.request_id,32)||!isHex(c.nonce,64)||
    !isHex(c.parent_grant_id,32)||!isHex(c.parent_grant_digest,64)||
    !isHex(c.parent_revision,64)||
    c.origin!==o.origin||c.service!==o.service||c.audience!==o.audience||
    !isHex(c.holder_public_key,64)||!isHex(c.installation_id,32)||
    (!o.deferIdentity&&(c.holder_public_key!==o.holderPublicKey||c.installation_id!==o.installationId))||
    c.session_installation_id!==o.sessionInstallationId||
    c.session_public_key!==this.#public||c.session_public_key===c.holder_public_key||
    !Array.isArray(c.permissions)||c.permissions.length<1||c.permissions.length>64||!c.permissions.every(plain)||
    ![c.issued_at,c.review_deadline,c.expires_at].every(integer)||
    c.issued_at<this.#created-30||c.issued_at>now||c.expires_at<=now||
    c.expires_at-c.issued_at>o.ttlSeconds||(!resuming&&c.review_deadline<=now)||
    c.review_deadline!==Math.min(c.issued_at+120,c.expires_at))invalid();
  const pin=o.profile?.presentations?.find(p=>p.issuer===c.issuer&&p.key_id===c.key_id&&p.audience===c.audience&&p.callers?.some(caller=>caller.kind==='browser'&&caller.id===o.origin));
  if(!pin||!isHex(pin.public_key,64))invalid();
  this.#challenge=c;this.#pin=structuredClone(pin);
  // Login learns the public holder binding only from the Wallet-reviewed
  // challenge. The temporary key already belongs to this browser client.
  if(o.deferIdentity){o.holderPublicKey=c.holder_public_key;o.installationId=c.installation_id;o.deferIdentity=false;}
 }
 async prepare(value){
  this.#require('created');this.#admitChallenge(value);this.#state='preparing';
  try{
   const signature=await this.#sign('secs.holder-session-possession.v1/signature',this.#challenge);
   this.#require('preparing');if(this.#time()>=this.#challenge.review_deadline)invalid();this.#state='reviewed';return signature;
  }catch(error){this.dispose();throw error;}
 }
 async accept(value){
  this.#require('reviewed');
  try{
   const credential=structuredClone(value);
   if(!shape(credential,['claims','signature'])||!shape(credential.claims,['schema','credential_id','challenge_digest','challenge'])||
     credential.claims.schema!=='secs.holder-session-credential.v1'||!isHex(credential.claims.credential_id,32)||!isHex(credential.signature,128)||
     canonicalSessionJson(credential.claims.challenge)!==canonicalSessionJson(this.#challenge))invalid();
   this.#state='accepting';
   if(credential.claims.challenge_digest!==await this.#digest(this.#challenge))invalid();this.#require('accepting');
   const crypto=this.#options.cryptoImpl;
   const key=await crypto.subtle.importKey('raw',unhex(this.#pin.public_key),'Ed25519',false,['verify']);
   if(!await crypto.subtle.verify('Ed25519',key,unhex(credential.signature),transcript('secs.holder-session-credential.v1/signature',credential.claims)))invalid();
   this.#require('accepting');this.#credential=credential;this.#state='ready';
  }catch(error){this.dispose();throw error;}
 }
 async present(value){
  this.#require('ready');const call=structuredClone(value);
  if(!shape(call,['operation','resource','parameters'])||typeof call.operation!=='string'||typeof call.resource!=='string'||!plain(call.parameters))invalid();
  canonicalSessionJson(call);
  const credential=structuredClone(this.#credential),now=this.#time();
  const expires=this.#challenge.expires_at,credentialDigest=await this.#digest(credential),callDigest=await this.#digest(call);this.#require('ready');
  const proof={schema:'secs.holder-session-presentation.v1',credential_digest:credentialDigest,call_digest:callDigest,nonce:hex(this.#options.cryptoImpl.getRandomValues(new Uint8Array(16))),issued_at:now,expires_at:Math.min(now+60,expires)};
  const signature=await this.#sign('secs.holder-session-presentation.v1/signature',proof);
  this.#require('ready');return{credential,call,proof:{...proof,signature}};
 }
 async status(){
  const now=this.#time(),state=this.#state;
  if(!['reviewed','ready'].includes(state))fail('holder_session_state');
  const c=this.#challenge;
  const request={schema:'secs.holder-session-status.v1',request_id:c.request_id,challenge_id:c.challenge_id,
    nonce:hex(this.#options.cryptoImpl.getRandomValues(new Uint8Array(16))),issued_at:now,expires_at:Math.min(now+60,c.expires_at)};
  const signature=await this.#sign('secs.holder-session-status.v1/signature',request);
  this.#require(state);return{...request,signature};
 }
 /** Structured-clone checkpoint of the finite child only, never root custody.
  * The private CryptoKey remains nonextractable; no seed/JWK is produced. */
 checkpoint(){
  const now=this.#time();if(!['reviewed','ready'].includes(this.#state))fail('holder_session_state');
  const o=this.#options;
  return {schema:'devgraph.browser-session.v1',privateKey:this.#key,publicKey:this.#public,
   sessionInstallationId:o.sessionInstallationId,origin:o.origin,service:o.service,audience:o.audience,
   holderPublicKey:o.holderPublicKey,installationId:o.installationId,profile:structuredClone(o.profile),
   ttlSeconds:o.ttlSeconds,createdAt:this.#created,lastObservedAt:now,
   challenge:structuredClone(this.#challenge),credential:structuredClone(this.#credential)};
 }
 static async restore(record,{origin,service,profile,audience='devgraph',cryptoImpl=globalThis.crypto,now=()=>Math.floor(Date.now()/1000),lifecycle=globalThis}){
  const fields=['schema','privateKey','publicKey','sessionInstallationId','origin','service','audience','holderPublicKey','installationId','profile','ttlSeconds','createdAt','lastObservedAt','challenge','credential'];
  if(!shape(record,fields)||record.schema!=='devgraph.browser-session.v1'||record.origin!==origin||record.service!==service||record.audience!==audience||
   canonicalSessionJson(record.profile)!==canonicalSessionJson(profile)||!isHex(record.publicKey,64)||!isHex(record.sessionInstallationId,32)||
   !isHex(record.holderPublicKey,64)||!isHex(record.installationId,32)||!integer(record.createdAt)||!integer(record.lastObservedAt)||
   record.lastObservedAt<record.createdAt||record.lastObservedAt>now()||!Number.isSafeInteger(record.ttlSeconds)||record.ttlSeconds<1||record.ttlSeconds>3600||
   record.privateKey?.type!=='private'||record.privateKey.extractable!==false||record.privateKey.algorithm?.name!=='Ed25519'||
   JSON.stringify(record.privateKey.usages)!=='["sign"]')invalid();
  // Prove that the structured-cloned private handle matches the recorded key.
  // A copied or corrupt metadata record cannot substitute a different signer.
  const publicKey=await cryptoImpl.subtle.importKey('raw',unhex(record.publicKey),'Ed25519',false,['verify']);
  const probe=cryptoImpl.getRandomValues(new Uint8Array(32));
  const signature=await cryptoImpl.subtle.sign('Ed25519',record.privateKey,probe);
  if(!await cryptoImpl.subtle.verify('Ed25519',publicKey,signature,probe))invalid();
  const session=new BrowserHolderSession(record.privateKey,record.publicKey,{origin,service,profile:structuredClone(profile),audience,
   holderPublicKey:record.holderPublicKey,installationId:record.installationId,sessionInstallationId:record.sessionInstallationId,
   ttlSeconds:record.ttlSeconds,cryptoImpl,now,lifecycle},record.createdAt);
  try{
   session.#last=record.lastObservedAt;session.#admitChallenge(record.challenge,true);session.#state='reviewed';
   if(record.credential!==null)await session.accept(record.credential);
   return session;
  }catch(error){session.dispose();throw error;}
 }
 dispose(){this.#options.lifecycle?.removeEventListener?.('pagehide',this.#hide);this.#key=null;this.#challenge=null;this.#credential=null;this.#pin=null;this.#state='closed';}
}

async function createSession({origin,service,holderPublicKey,installationId,profile,ttlSeconds,audience='devgraph',cryptoImpl=globalThis.crypto,now=()=>Math.floor(Date.now()/1000),lifecycle=globalThis,deferIdentity=false}){
 if(typeof origin!=='string'||new URL(origin).origin!==origin||new URL(origin).protocol!=='https:'||
    typeof service!=='string'||!service||service.length>256||(!deferIdentity&&(!isHex(holderPublicKey,64)||!isHex(installationId,32)))||
    !Number.isSafeInteger(ttlSeconds)||ttlSeconds<1||ttlSeconds>3600||typeof audience!=='string'||!audience)invalid();
 const created=now();if(!integer(created))invalid();
 const keys=await cryptoImpl.subtle.generateKey('Ed25519',false,['sign','verify']);
 if(keys.privateKey.extractable)invalid();
 const publicKey=hex(await cryptoImpl.subtle.exportKey('raw',keys.publicKey));
 const sessionInstallationId=hex(cryptoImpl.getRandomValues(new Uint8Array(16)));
 return new BrowserHolderSession(keys.privateKey,publicKey,{origin,service,holderPublicKey,installationId,sessionInstallationId,profile:structuredClone(profile),ttlSeconds,audience,cryptoImpl,now,lifecycle,deferIdentity},created);
}
export const createBrowserHolderSession=options=>createSession(options);
export const createBrowserLoginSession=options=>createSession({...options,holderPublicKey:null,installationId:null,deferIdentity:true});
export const restoreBrowserHolderSession=(record,options)=>BrowserHolderSession.restore(record,options);
