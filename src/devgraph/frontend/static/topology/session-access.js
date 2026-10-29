/* Public monitor session: Wallet is needed only for explicit sign in. */
(function(root){
 'use strict';
 root.DevgraphWalletAccess={mount(element,{onChange=()=>{}}={}){
  let client=null,settled=false,leaving=false,clock=null,logoutFailed=false;
  const status=element.querySelector('[data-session-status]'),expiry=element.querySelector('[data-session-expiry]'),logout=element.querySelector('[data-session-logout]');
  let returnPath=()=>'/monitor',channel=null;
  function redirect(){if(!leaving){leaving=true;root.location.replace('/login?return='+encodeURIComponent(returnPath(root.location.pathname+root.location.hash)));}}
  function render(){
   const ready=client?.stage==='ready';status.textContent=ready?'Signed in with Wallet':'Checking session…';
   expiry.textContent=ready?'Session ends '+new Date(client.expiresAt*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):'';
   if(logoutFailed)status.textContent='Could not clear this browser session. Retry sign out before leaving this device.';
   logout.disabled=!ready&&!logoutFailed;logout.textContent=logoutFailed?'Retry sign out':'Sign out';onChange(controller);
   if(settled&&!ready)redirect();
  }
  const controller={get authorityOrigin(){return client?.authorityOrigin??root.location.origin;},get routePrefix(){return client?.serviceBinding?.claims.prefix??'';},get requestIdentity(){return client?.identity??null;},get accessEndpoint(){return client?.configuration?.access_requests_endpoint??null;},get sessionKey(){return client?.sessionKey??'';},get preferenceSeed(){return client?.preferenceSeed??'';},get connected(){return client?.stage==='ready';},
   read:(path,options)=>client?client.read(path,options):Promise.reject(Object.assign(Error('wallet_read_access_required'),{status:401})),
   // Existing monitor/board pagehide calls detach custody, never sign out.
   disconnect:()=>{settled=false;client?.disconnect();},
   logout:async()=>{leaving=true;logoutFailed=false;try{await client?.logout();try{channel?.postMessage('signed_out');}catch{}root.location.replace('/login');}catch{logoutFailed=true;render();}}
  };
  logout.addEventListener('click',()=>void controller.logout());
  if(root.BroadcastChannel){channel=new root.BroadcastChannel('devgraph-session-events-v1');channel.onmessage=event=>{if(event.data==='signed_out'){client?.disconnect();redirect();}};}
  async function resume(){
   settled=false;
   try{
    const module=await import('/assets/credential-v2/session-login.mjs');
    ({loginReturnPath:returnPath}=await import('/assets/credential-v2/session-store.mjs'));
    client?.disconnect();client=new module.WalletLoginClient({onChange:render});
    await client.resumeLogin();settled=true;render();
   }catch{redirect();}
  }
  root.addEventListener('pagehide',()=>{settled=false;root.clearInterval(clock);client?.disconnect();channel?.close();});
  root.addEventListener('pageshow',event=>{if(event.persisted){leaving=false;void resume();clock=root.setInterval(()=>client?.expireIfNeeded(),1000);}});
  clock=root.setInterval(()=>client?.expireIfNeeded(),1000);
  void resume();return controller;
 }};
})(globalThis);
