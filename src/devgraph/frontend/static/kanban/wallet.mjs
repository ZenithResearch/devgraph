const b64=bytes=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
const unb64=text=>Uint8Array.from(atob(text.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
export class Wallet {
 constructor(provider=globalThis.castaliaWallet){this.provider=provider;this.connection=null;this.busy=false;}
 async call(action,fields={}) {if(!this.provider?.devgraph)throw Object.assign(Error('Castalia Wallet is not available in this browser.'),{dispatched:false});return this.provider.devgraph({v:1,request_id:crypto.randomUUID(),action,...(this.connection?{connection_id:this.connection.connection_id}:{}),...fields});}
 async connect(){const result=await this.call('connect');this.connection=result;if(!result.capabilities?.includes('workflow.v1')){await this.dispose();throw Object.assign(Error('This Wallet bridge needs the Kanban workflow update. Reading remains available.'),{dispatched:false});}return result;}
 async dispose(){try{if(this.connection)await this.call('dispose');}finally{this.connection=null;}}
 async execute(raw,key){
  if(this.busy)throw Object.assign(Error('Finish the pending change first.'),{dispatched:false});
  if(!this.connection)throw Object.assign(Error('Connect Wallet before moving work.'),{dispatched:false});
  this.busy=true;let dispatched=false;
  try {
   const authorization=await this.call('authorize',{request_b64:b64(new TextEncoder().encode(raw)),idempotency_key:key});
   dispatched=true;
   const header=await this.call('execute',{authorization_id:authorization.authorization_id});
   if(header.dispatched!==true||header.content_encoding!=='identity'||!Number.isSafeInteger(header.limit)||header.limit>8388608)throw Error('Invalid mutation response.');
   let seq=-1,total=0;const chunks=[];
   for(;;){const part=await this.call('pull',{stream_id:header.stream_id,ack_seq:seq});const bytes=unb64(part.chunk_b64);if(part.stream_id!==header.stream_id||part.seq!==seq+1||part.total!==total+bytes.length||part.total>header.limit||bytes.length>49152||typeof part.done!=='boolean')throw Error('Incomplete mutation response.');seq=part.seq;total=part.total;chunks.push(bytes);if(part.done)break;}
   const merged=new Uint8Array(total);let offset=0;for(const bytes of chunks){merged.set(bytes,offset);offset+=bytes.length;}
   const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(merged));
   if(header.status>=400){const error=Error(header.status===412?'This item changed. Refresh and review the new version.':value.title||'The change was declined.');error.status=header.status;error.dispatched=header.status>=500;throw error;}
   const subject=JSON.parse(raw);if(!value.receipt?.receipt_id||value.receipt.operation!==`devgraph.work.${subject.operation}.v1`||value.receipt.subject_label!==subject.kind||value.receipt.subject_id!==subject.id||typeof value.receipt.duplicate!=='boolean')throw Error('The committed result could not be confirmed.');return value;
  }catch(error){if(typeof error.dispatched!=='boolean')error.dispatched=dispatched;throw error;}finally{this.busy=false;}
 }
}
