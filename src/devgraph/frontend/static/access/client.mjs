const plain=v=>!!v&&Object.getPrototypeOf(v)===Object.prototype;
const id=()=>crypto.randomUUID().replaceAll('-','');
export class AccessOperatorClient{
 constructor({wallet,fetchImpl=fetch,storage=globalThis.sessionStorage,origin=location.origin}={}){
  Object.assign(this,{wallet,fetchImpl,storage,origin,pending:null,state:'idle'});
  this.key='devgraph.operator-pending.v1:'+origin;
  const saved=storage?.getItem(this.key);
  if(saved){try{const p=JSON.parse(saved);if(!plain(p)||p.schema!=='devgraph.operator-pending.v1'||p.origin!==origin||!plain(p.action)||!/^[a-f0-9]{32}$/.test(p.action.action_id))throw Error();this.pending=p;this.state=p.dispatched===false?'idle':'unknown';}catch{throw Error('invalid_recovery_record');}}
 }
 async post(path,body){
  const fetchImpl=this.fetchImpl;const response=await fetchImpl(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer'});
  const result=await response.json();if(!response.ok)throw Object.assign(Error(result.error??'access_request_failed'),{status:response.status});return result;
 }
 persist(){if(!this.storage)throw Error('recovery_storage_required');this.storage.setItem(this.key,JSON.stringify(this.recovering?this.original:this.pending));}
 async prepare(action){
  if(this.state==='unknown')throw Error('recover_original_action_first');
  const identity=this.wallet.requestIdentity;if(!identity)throw Error('wallet_connection_required');
  const request={schema:'devgraph.access-operator-action.v1',action_id:id(),...structuredClone(action)};
  const challenge=await this.post('/devgraph/access-operator/v1/challenges',{...identity,action:request});
  if(!plain(challenge)||challenge.schema!=='castalia.access-request-challenge.v1'||challenge.receiver!==this.origin||challenge.subject_public_key!==identity.subject_public_key||challenge.installation_id!==identity.installation_id||challenge.details?.action?.action_id!==request.action_id)throw Error('invalid_operator_challenge');
  this.pending={schema:'devgraph.operator-pending.v1',origin:this.origin,identity,action:request,dispatched:false};this.challenge=challenge;this.persist();this.state='prepared';return challenge;
 }
 approve(){
  if(this.state!=='prepared')throw Error('prepare_operator_action_first');
  const challenge=this.challenge;
  // Start Wallet within the click gesture, before awaiting any network call.
  const consent=this.wallet.approveAccess(challenge);
  return consent.then(async approval=>{
   this.state='unknown';this.pending.dispatched=true;this.persist();
   let result;try{result=await this.post('/devgraph/access-operator/v1/actions',{challenge:approval.challenge,signature:approval.signature});}catch(error){if(!this.recovering&&[400,401,409].includes(error.status)&&['invalid_operator_selection','operator_access_denied','operator_request_changed'].includes(error.message)){this.storage.removeItem(this.key);this.pending=null;this.challenge=null;this.state='idle';throw error;}throw Object.assign(Error('operator_outcome_unknown'),{cause:error});}
   return this.accept(result);
  });
 }
 accept(result){
  if(!plain(result)||result.schema!=='devgraph.access-operator-result.v1'||result.action_id!==this.pending.action.action_id||result.authority_issued!==false)throw Error('operator_outcome_unknown');
  if(this.recovering){
   if(result.original_action_id!==this.original.action.action_id||result.status!=='reconciled'||!plain(result.result)||result.result.action_id!==this.original.action.action_id){this.pending=this.original;this.recovering=false;this.state='unknown';this.persist();throw Error('operator_outcome_unknown');}
   result=result.result;
  }
  const action=this.recovering?this.original.action:this.pending.action;
  const valid=action.action==='inbox'?plain(result.inbox)&&Array.isArray(result.inbox.items)&&Number.isSafeInteger(result.inbox.total)&&result.inbox.total>=result.inbox.items.length&&result.ceiling?.schema==='devgraph.access-draft.v2'&&/^[a-f0-9]{64}$/.test(result.ceiling_digest_sha256):action.action==='review'?result.request_id===action.request_id&&result.review_id===action.action_id&&result.expected_version===action.expected_version&&/^[a-f0-9]{64}$/.test(result.selection_digest_sha256)&&plain(result.selection)&&plain(result.plans):['approve','deny'].includes(action.action)?result.request_id===action.request_id&&result.version===2&&result.status===(action.action==='approve'?'approved_pending_activation':'denied'):false;
  if(!valid||result.authority_issued!==false)throw Error('operator_outcome_unknown');
  this.storage.removeItem(this.key);this.pending=null;this.challenge=null;this.recovering=false;this.state='idle';return result;
 }
 async prepareRecovery(){
  if(this.state!=='unknown'||!this.pending)throw Error('no_unknown_action');
  const original=structuredClone(this.recovering?this.original:this.pending);this.state='idle';this.original=original;this.recovering=true;
  try{await this.prepare({action:'status',original_action_id:original.action.action_id});}
  catch(error){this.pending=original;this.state='unknown';this.recovering=false;this.persist();throw error;}
 }
}
