/** Origin-local, structured-clone child custody. Never JSON/JWK/private root. */
const unavailable=()=>Object.assign(Error('session_storage_unavailable'),{code:'session_storage_unavailable'});
const changed=()=>Object.assign(Error('browser_session_changed'),{code:'browser_session_changed'});

export class BrowserSessionStore {
 constructor({indexedDB=globalThis.indexedDB}={}){this.indexedDB=indexedDB;}
 async transaction(mode,work){
  if(!this.indexedDB)throw unavailable();
  const database=await new Promise((resolve,reject)=>{
   let settled=false;
   const request=this.indexedDB.open('devgraph-browser-sessions-v1',1);
   request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains('sessions'))request.result.createObjectStore('sessions');};
   request.onsuccess=()=>{if(settled)request.result.close();else{settled=true;resolve(request.result);}};
   const fail=()=>{if(!settled){settled=true;reject(unavailable());}};
   request.onerror=fail;request.onblocked=fail;
  });
  return new Promise((resolve,reject)=>{
   let result,failed=false;
   const fail=error=>{if(failed)return;failed=true;database.close();reject(error);};
   let transaction;
   try{transaction=database.transaction('sessions',mode);}
   catch{database.close();reject(unavailable());return;}
   transaction.oncomplete=()=>{database.close();if(!failed)resolve(result);};
   transaction.onerror=()=>fail(unavailable());transaction.onabort=()=>fail(unavailable());
   try{work(transaction.objectStore('sessions'),value=>{result=value;},error=>{fail(error);try{transaction.abort();}catch{}});}
   catch(error){fail(error);try{transaction.abort();}catch{}}
  });
 }
 load(slot){return this.transaction('readonly',(store,done)=>{const read=store.get(slot);read.onsuccess=()=>done(read.result??null);});}
 reserve(slot,epoch){return this.transaction('readwrite',store=>{store.put({epoch,value:null},slot);});}
 save(slot,epoch,value){
  return this.transaction('readwrite',(store,done,fail)=>{
   const read=store.get(slot);read.onsuccess=()=>{
    if(read.result?.epoch!==epoch){fail(changed());return;}
    store.put({epoch,value},slot);done(true);
   };
  });
 }
 remove(slot,epoch){
  return this.transaction('readwrite',(store,done)=>{
   const read=store.get(slot);read.onsuccess=()=>{
    if(epoch===undefined||read.result?.epoch===epoch){store.delete(slot);done(true);}else done(false);
   };
  });
 }
}

/** Never redirect to a supplied foreign origin, API, asset, or arbitrary page. */
export function loginReturnPath(value){
 if(typeof value!=='string'||value.length>2048)return '/monitor';
 const matched=value.match(/^\/monitor(?:\/kanban\/?)?(?:#[a-z][a-z0-9-]{0,79})?$/);
 return matched?.[0]===value?value:'/monitor';
}
