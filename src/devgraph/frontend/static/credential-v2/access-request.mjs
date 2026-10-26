import{validateCatalog,preferenceFor}from'./scope-picker.mjs';
const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
export class AccessRequestClient{
 constructor({wallet,storage=globalThis.sessionStorage,origin=location.origin,fetchImpl=fetch}){
  Object.assign(this,{wallet,storage,origin:wallet.authorityOrigin??origin,fetchImpl,prepared:null,prefix:wallet.routePrefix??''});
  origin=this.origin;
  const identity=wallet.requestIdentity;if(!identity)throw Error('wallet_connection_required');
  this.identity=structuredClone(identity);this.key='devgraph.access-request.v1:'+origin+':'+identity.subject_public_key+':'+identity.installation_id;
  const saved=storage?.getItem(this.key);this.original=saved?JSON.parse(saved):null;
  if(this.original&&(!['devgraph.access-request-action.v1','devgraph.access-request-action.v2'].includes(this.original.schema)||!/^[a-f0-9]{32}$/.test(this.original.request_id)||this.original.subject_public_key!==identity.subject_public_key||this.original.installation_id!==identity.installation_id))throw Error('invalid_request_recovery_record');
 }
 startNew(){
  if(!this.original||!this.lastResult||!['active','denied','expired','revoked','superseded','partially_active'].includes(this.lastResult.status))throw Error('recover_original_request_first');
  this.storage.setItem(this.key+':history:'+this.original.request_id,JSON.stringify(this.original));this.storage.removeItem(this.key);this.original=null;this.action=null;this.prepared=null;this.attempted=false;this.lastResult=null;
 }
 async post(suffix,body){const fetchImpl=this.fetchImpl;const r=await fetchImpl(this.prefix+'/devgraph/access-requests/v2/'+suffix,{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'},credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'});const data=await r.json();if(!r.ok)throw Error(data.error??'access_request_failed');return data;}
 async loadCatalog(){
  const fetchImpl=this.fetchImpl;const response=await fetchImpl(this.prefix+'/devgraph/access-requests/v2/catalog',{credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'});if(!response.ok)throw Error('access_catalog_unavailable');
  const value=validateCatalog(await response.json(),this.origin),body=structuredClone(value);delete body.scopes;delete body.scope_semantics;delete body.catalog_digest_sha256;const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(body))))].map(x=>x.toString(16).padStart(2,'0')).join('');if(digest!==value.catalog_digest_sha256)throw Error('invalid_access_catalog');this.catalog=value;return this.catalog;
 }
 async prepare({presetID='',resourcePaths=[],catalog=null}={}){
  const fresh=!this.original;
  if(fresh){this.original={schema:catalog?'devgraph.access-request-action.v2':'devgraph.access-request-action.v1',action:'submit',request_id:crypto.randomUUID().replaceAll('-',''),...this.identity,intent:catalog?{schema:'devgraph.access-intent.v2',preference:preferenceFor(validateCatalog(catalog,this.origin),presetID,resourcePaths)}:null};if(!this.storage)throw Error('recovery_storage_required');this.action=this.original;}
  else if(!(this.action?.action==='submit'&&!this.attempted))this.action={...this.original,action:'status',intent:null};
  try{const challenge=await this.post('challenges',{request:this.action});
  if(challenge?.schema!=='castalia.access-request-challenge.v1'||challenge.receiver!==this.origin||stable(challenge.details)!==stable(this.action))throw Error('invalid_access_challenge');
  // A rejected unsigned preparation cannot trap a stale catalog preference.
  // Persist exact bytes before Wallet consent and every possible submission.
  if(fresh)this.storage.setItem(this.key,JSON.stringify(this.original));
  this.prepared=challenge;return challenge;}catch(error){if(fresh){this.original=null;this.action=null;}throw error;}
 }
 approve(){
  if(!this.prepared)throw Error('prepare_request_first');
  const challenge=this.prepared;this.prepared=null;
  const consent=this.wallet.approveAccess(challenge);
  return consent.then(async approval=>{
   this.attempted=true;let result;try{result=await this.post('actions',approval);}catch(error){throw Object.assign(Error('access_request_outcome_unknown'),{cause:error});}
   if(!result||!['devgraph.access-request-result.v1','devgraph.access-request-result.v2','devgraph.access-request-result.v3','devgraph.access-request-result.v4'].includes(result.schema)||result.request_id!==this.original.request_id||result.authority_issued!==false||!['pending','expired','unknown','approved_pending_activation','denied','active','revoked','superseded','partially_active'].includes(result.status))throw Error('access_request_outcome_unknown');
   if(['devgraph.access-request-result.v3','devgraph.access-request-result.v4'].includes(result.schema)&&result.grant_active!==(result.status==='active'))throw Error('access_request_outcome_unknown');
   this.lastResult=result;return result;
  });
 }
}
