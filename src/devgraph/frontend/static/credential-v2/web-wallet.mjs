/** Window-bound Wallet provider. This channel never carries custody or recovery inputs. */
const CHANNEL='castalia.wallet.window.v1';
export class WebWalletProvider {
 constructor({window,popup,url,restore=false,mode='window',onDisconnect=()=>{},onDispose=()=>{}}){
  const target=new URL(url);
  if(mode!=='window'&&mode!=='embed')throw Error('wallet_embed_invalid');
  if(target.protocol!=='https:'||target.username||target.password||(mode==='embed'&&target.origin===window.location?.origin))throw Error('wallet_embed_invalid');
  this.mode=mode;this.channel=mode==='embed'?'castalia.wallet.embed.v1':CHANNEL;this.onDispose=onDispose;
  this.window=window;this.popup=popup;this.origin=new URL(url).origin;this.pending=new Map();this.sequence=0;this.disposed=false;this.onDisconnect=onDisconnect;
  this.nonce=window.crypto.randomUUID();
  this.receive=event=>{
   const m=event.data;if(event.source!==this.popup||event.origin!==this.origin||!m||m.channel!==this.channel||m.nonce!==this.nonce)return;
   if(m.type==='ready'){window.clearInterval(this.timer);this.resolveReady?.();return;}
   if(m.type==='available'){window.clearTimeout(this.deadline);this.deadline=window.setTimeout(()=>this.dispose(),600000);return;}
   if(m.type==='unavailable'){this.rejectReady?.(Error('wallet_embed_setup_required'));this.dispose();return;}
   if(m.type==='locked'){this.onDisconnect();return;}
   if(m.type!=='response'||!Number.isSafeInteger(m.id))return;
   const pending=this.pending.get(m.id);if(!pending)return;this.pending.delete(m.id);window.clearTimeout(pending.timer);
   if(m.ok===true)pending.resolve(m.value);else pending.reject(Object.assign(Error(m.error||'authorization_failed'),{code:m.error||'authorization_failed'}));
  };
  window.addEventListener('message',this.receive);
  this.readyPromise=new Promise((resolve,reject)=>{this.resolveReady=resolve;this.rejectReady=reject;});
  this.readyPromise.catch(()=>{});
  this.timer=window.setInterval(()=>{if(this.popup.closed===true){this.dispose();return;}this.popup.postMessage({channel:this.channel,type:'hello',nonce:this.nonce,restore},this.origin);},250);
  this.deadline=window.setTimeout(()=>{this.rejectReady?.(Error('wallet_embed_setup_required'));this.dispose();},15000);
 }
 async ready(){await this.readyPromise;this.window.clearTimeout(this.deadline);}
 request(method,params){
  if(this.disposed||this.popup.closed===true)return Promise.reject(Error('wallet_connection_changed'));
  const id=++this.sequence;
  return new Promise((resolve,reject)=>{const timer=this.window.setTimeout(()=>{this.pending.delete(id);reject(Error('wallet_request_timed_out'));},125000);this.pending.set(id,{resolve,reject,timer});this.popup.postMessage({channel:this.channel,type:'request',nonce:this.nonce,id,method,params},this.origin);});
 }
 getCapabilities(){return this.request('getCapabilities');}
 proposeProviderProfile(profile){return this.request('proposeProviderProfile',profile);}
 requestConnection(){return this.request('requestConnection');}
 readAccess(input){return this.request('readAccess',input);}
 dispose(){
  if(this.disposed)return;this.disposed=true;
  this.window.clearInterval(this.timer);this.window.clearTimeout(this.deadline);this.window.removeEventListener('message',this.receive);
  this.rejectReady?.(Error('wallet_connection_changed'));
  for(const p of this.pending.values()){this.window.clearTimeout(p.timer);p.reject(Error('wallet_connection_changed'));}this.pending.clear();
  if(this.mode==='window'&&this.popup.closed!==true)this.popup.close();this.onDispose();this.onDisconnect();
 }
}
