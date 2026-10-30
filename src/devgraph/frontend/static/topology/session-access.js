/* Public monitor session: Wallet is needed only for explicit sign in. */
(function(root){
 'use strict';
 root.DevgraphWalletAccess={mount(element,{onChange=()=>{}}={}){
  let client=null,settled=false,leaving=false,clock=null,logoutFailed=false,recoveryTimer=null,recoveryAttempts=0,resuming=false,resumeEpoch=0;
  const status=element.querySelector('[data-session-status]'),expiry=element.querySelector('[data-session-expiry]'),logout=element.querySelector('[data-session-logout]'),retry=element.querySelector('[data-session-retry]');
  let returnPath=()=>'/monitor',channel=null;
  function redirect(){root.clearTimeout(recoveryTimer);if(!leaving){leaving=true;root.location.replace('/login?return='+encodeURIComponent(returnPath(root.location.pathname+root.location.hash)));}}
  function render(){
   const ready=client?.stage==='ready',recovering=client?.stage==='session_recovery';status.textContent=ready?'Signed in with Wallet':recovering?'Connection paused · your existing sign-in is preserved':'Checking session…';
   if(retry){retry.hidden=!recovering;retry.disabled=resuming;}
   expiry.textContent=ready?'Session ends '+new Date(client.expiresAt*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):'';
   if(logoutFailed)status.textContent='Could not clear this browser session. Retry sign out before leaving this device.';
   logout.disabled=!ready&&!recovering&&!logoutFailed;logout.textContent=logoutFailed?'Retry sign out':'Sign out';onChange(controller);
   if(ready){root.clearTimeout(recoveryTimer);recoveryTimer=null;recoveryAttempts=0;}
   if(settled&&!ready){
    if(recovering){
     // Reconcile the same child key/result, never open Wallet or renew expiry.
     if(!recoveryTimer&&recoveryAttempts<3&&!leaving){
      const delay=[2000,5000,10000][recoveryAttempts++];
      recoveryTimer=root.setTimeout(()=>{recoveryTimer=null;void resume();},delay);
     }
    }else redirect();
   }
  }
  const controller={get authorityOrigin(){return client?.authorityOrigin??root.location.origin;},get routePrefix(){return client?.serviceBinding?.claims.prefix??'';},get requestIdentity(){return client?.identity??null;},get accessEndpoint(){return client?.configuration?.access_requests_endpoint??null;},get sessionKey(){return client?.sessionKey??'';},get preferenceSeed(){return client?.preferenceSeed??'';},get connected(){return client?.stage==='ready';},
   read:(path,options)=>client?client.read(path,options):Promise.reject(Object.assign(Error('wallet_read_access_required'),{status:401})),
   // Existing monitor/board pagehide calls detach custody, never sign out.
   disconnect:()=>{settled=false;client?.disconnect();},
   logout:async()=>{leaving=true;logoutFailed=false;try{await client?.logout();try{channel?.postMessage('signed_out');}catch{}root.location.replace('/login');}catch{logoutFailed=true;render();}}
  };
  logout.addEventListener('click',()=>void controller.logout());
  retry?.addEventListener('click',()=>{root.clearTimeout(recoveryTimer);recoveryTimer=null;recoveryAttempts=0;void resume();});
  root.addEventListener('online',()=>{if(client?.stage==='session_recovery'&&!leaving)void resume();});
  function openChannel(){if(root.BroadcastChannel){channel?.close();channel=new root.BroadcastChannel('devgraph-session-events-v1');channel.onmessage=event=>{if(event.data==='signed_out'){client?.disconnect();redirect();}};}}
  openChannel();
  async function resume(){
   if(resuming||leaving)return;
   resuming=true;settled=false;const epoch=++resumeEpoch;
   try{
    const module=await import('/assets/credential-v2/session-login.mjs');
    ({loginReturnPath:returnPath}=await import('/assets/credential-v2/session-store.mjs'));
    if(leaving||epoch!==resumeEpoch)return;
    client?.disconnect();client=new module.WalletLoginClient({onChange:render});
    await client.resumeLogin();
    if(leaving||epoch!==resumeEpoch)return;
    settled=true;
   }catch{if(!leaving&&epoch===resumeEpoch)redirect();}
   finally{if(epoch===resumeEpoch){resuming=false;if(!leaving)render();}}
  }
  root.addEventListener('pagehide',()=>{leaving=true;resumeEpoch++;resuming=false;settled=false;root.clearTimeout(recoveryTimer);recoveryTimer=null;root.clearInterval(clock);client?.disconnect();channel?.close();});
  root.addEventListener('pageshow',event=>{if(event.persisted){leaving=false;openChannel();void resume();clock=root.setInterval(()=>client?.expireIfNeeded(),1000);}});
  clock=root.setInterval(()=>client?.expireIfNeeded(),1000);
  void resume();return controller;
 }};
})(globalThis);
