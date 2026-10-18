const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
export class AccessRequestClient{
 constructor({wallet,storage=globalThis.sessionStorage,origin=location.origin,fetchImpl=fetch}){
  Object.assign(this,{wallet,storage,origin,fetchImpl,prepared:null});
  const identity=wallet.requestIdentity;if(!identity)throw Error('wallet_connection_required');
  this.identity=structuredClone(identity);this.key='devgraph.access-request.v1:'+origin+':'+identity.subject_public_key+':'+identity.installation_id;
  const saved=storage?.getItem(this.key);this.original=saved?JSON.parse(saved):null;
  if(this.original&&(this.original.schema!=='devgraph.access-request-action.v1'||!/^[a-f0-9]{32}$/.test(this.original.request_id)||this.original.subject_public_key!==identity.subject_public_key||this.original.installation_id!==identity.installation_id))throw Error('invalid_request_recovery_record');
 }
 async post(suffix,body){const fetchImpl=this.fetchImpl;const r=await fetchImpl('/devgraph/access-requests/v2/'+suffix,{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'},credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'});const data=await r.json();if(!r.ok)throw Error(data.error??'access_request_failed');return data;}
 async prepare(){
  if(!this.original){this.original={schema:'devgraph.access-request-action.v1',action:'submit',request_id:crypto.randomUUID().replaceAll('-',''),...this.identity,intent:null};if(!this.storage)throw Error('recovery_storage_required');this.storage.setItem(this.key,JSON.stringify(this.original));this.action=this.original;}
  else if(!(this.action?.action==='submit'&&!this.attempted))this.action={...this.original,action:'status',intent:null};
  const challenge=await this.post('challenges',{request:this.action});
  if(challenge?.schema!=='castalia.access-request-challenge.v1'||challenge.receiver!==this.origin||stable(challenge.details)!==stable(this.action))throw Error('invalid_access_challenge');
  this.prepared=challenge;return challenge;
 }
 approve(){
  if(!this.prepared)throw Error('prepare_request_first');
  const challenge=this.prepared;this.prepared=null;
  const consent=this.wallet.approveAccess(challenge);
  return consent.then(async approval=>{
   this.attempted=true;let result;try{result=await this.post('actions',approval);}catch(error){throw Object.assign(Error('access_request_outcome_unknown'),{cause:error});}
   if(!result||!['devgraph.access-request-result.v1','devgraph.access-request-result.v2'].includes(result.schema)||result.request_id!==this.original.request_id||result.authority_issued!==false||!['pending','expired','unknown','approved_pending_activation','denied'].includes(result.status))throw Error('access_request_outcome_unknown');
   return result;
  });
 }
}
