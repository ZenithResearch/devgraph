/* Wallet-owned login/import, application-owned read transport. No key inputs. */
(function(root){
'use strict';

const messages={wallet_request_upgrade_required:'Update Castalia Wallet to send access requests. Your read-only connection is unchanged.',access_request_outcome_unknown:'Request outcome unknown. Check its original status; no new request will be submitted.',wallet_embed_setup_required:'Embedded Wallet is unavailable. Ask the Wallet administrator to enable this server, or use the Chrome Wallet extension.',wallet_connection_changed:'Wallet connection closed. Choose Wallet to reconnect.',wallet_unlock_required:'Unlock Wallet, then continue.',wallet_connection_required:'Connect Wallet to this site, then continue.',wallet_upgrade_required:'Update the installed Castalia Wallet to enable read access. You can also choose the embedded Wallet.',wallet_read_setup_required:'This server needs Wallet read access enabled by its administrator.',wallet_read_denied:'This Wallet has no current access to the requested work. Ask the server administrator to review your scope using the public connection details below, then choose Check access.',membership_selection_required:'Choose the membership you want to use.',membership_unavailable:'Add an active membership in Wallet, then connect again.',membership_required:'Add an active membership in Wallet, then connect again.',origin_not_approved:'Approve this site in Wallet before connecting.',user_denied:'Connection cancelled. Nothing was shared.',approval_denied:'Read access cancelled. Nothing was shared.',wallet_locked:'Unlock Wallet, then connect again.',read_access_required:'Connect Wallet again to read your shared work.',wallet_read_access_required:'Connect Wallet again to read your shared work.',read_access_expired:'Your read access expired. Connect Wallet to review fresh access.'};
root.DevgraphWalletAccess={mount(element,{onChange=()=>{},provider=()=>root.castaliaWallet}={}){
 let requester=null,AccessRequestClient=null,requestMessage='';
 const get=name=>element.querySelector(`[data-wallet-${name}]`);
 let client=null,busy=false,ready=false,error='',kind='',pendingBridge=null,frame=null,discovery=0,chosen=false,webEpoch=0;
 const buttons={access:'Check access',profile:'Review server in Wallet',connection:'Connect Wallet',approval:'Allow read access in Wallet',ready:'Connected'};
 const clearError=()=>{error='';};
 function render(){
  const stage=client?.stage??'loading',selected=['profile','connection','access','approval','ready'].includes(stage);
  get('chrome').hidden=selected||!!frame||!provider();get('web').hidden=selected||!!frame;
  get('advance').hidden=!selected||stage==='ready';get('advance').disabled=busy;
  get('advance').textContent=buttons[stage]??'Continue in Wallet';
  get('disconnect').hidden=!selected&&!frame;get('disconnect').disabled=busy&&!frame;get('disconnect').textContent=frame&&!selected?'Cancel':'Disconnect';
  get('chrome').disabled=get('web').disabled=busy||!ready;
  get('membership-label').hidden=!['approval','access','ready'].includes(stage)||client.memberships.length<2;
  if(['approval','access','ready'].includes(stage)&&client.memberships.length>1){const select=get('membership');if(select.options.length!==client.memberships.length+1){select.replaceChildren(new Option('Choose membership',''),...client.memberships.map(p=>new Option(p.displayName,p.profileId)));}select.value=client.profileId??'';}
  get('access-details').hidden=stage!=='access'||!client?.connectionDetails;
  get('access-json').textContent=client?.connectionDetails?JSON.stringify(client.connectionDetails,null,2):'';
  get('copy').disabled=busy;
  get('request').hidden=!client?.identity||!client?.configuration?.access_requests_endpoint;get('request').disabled=busy;get('request').textContent=requester?.prepared?'Continue in Wallet':requester?.original?'Check request status':'Request access';
  get('status').textContent=error||requestMessage||(frame&&!selected?'Unlock or import your Wallet below. Your recovery file and password stay inside Castalia Wallet.':'')||(client?.notice==='read_scope_refreshed'?'Your access review expired. Permissions have been refreshed; choose Allow read access in Wallet to review them.':'')||({loading:'Preparing Wallet connection…',choose:'Connect your Wallet to view the work shared with you.',profile:'First, review this server’s public verification settings in Wallet.',access:'Checking the access assigned to this Wallet. If access has not been assigned, send the public details below to your server administrator.',connection:'Approve this site in Wallet. Your recovery file and password stay there.',approval:'Review the permitted work and expiry in Wallet.',ready:`Connected through ${kind==='chrome'?'Chrome Wallet':'Web Wallet'} · read only`}[stage]??'Ask the server administrator to enable Wallet read access.');
 }
 async function run(task){if(busy)return;busy=true;clearError();render();try{await task();}catch(e){error=messages[e?.code??e?.message]??'Could not connect. Check that Wallet is unlocked and your server is available.';}finally{busy=false;render();}}
 root.addEventListener('castalia:wallet:read-access-invalidated',()=>{if(kind==='chrome')client?.disconnect();});
 get('copy').onclick=()=>void run(async()=>{const details=client?.connectionDetails;if(!details)return;try{await root.navigator.clipboard.writeText(JSON.stringify(details,null,2));error='Public connection details copied. These details do not grant access.';}catch{error='Select and copy the public connection details below.';}});
 get('request').onclick=()=>void run(async()=>{
  if(!client?.identity||!AccessRequestClient)throw Error('wallet_connection_required');
  if(!requester||requester.identity.subject_public_key!==client.identity.subject_public_key||requester.identity.installation_id!==client.identity.installation_id)requester=new AccessRequestClient({wallet:controller});
  if(!requester.prepared){await requester.prepare();requestMessage=requester.action.action==='submit'?'Continue in Wallet to send your access request.':'Continue in Wallet to check your original request status.';}
  else{const result=await requester.approve();requestMessage=({active:'Read access approved and activated. Choose Check access to obtain your credential in Wallet.',revoked:'This read grant has been revoked. Request new access from the operator.',superseded:'A newer grant replaced this request. Choose Check access to review your current permissions.',pending:'Access requested. An operator must review your scope.',approved_pending_activation:'Selection approved; issuer activation is still required.',denied:'Your access request was declined.',expired:'This request expired. Ask the operator about requesting access again.',unknown:'The original result is unknown. Check status again; no request has been resubmitted.'})[result.status];}
 });
 get('advance').onclick=()=>void run(()=>client.advance());
 get('disconnect').onclick=()=>{clearError();webEpoch++;pendingBridge?.dispose();pendingBridge=null;frame?.remove();frame=null;get('embed').hidden=true;client.disconnect();render();};
 get('membership').onchange=event=>{if(client)client.profileId=event.target.value||undefined;};
 get('chrome').onclick=()=>{chosen=true;root.clearInterval(discovery);void run(async()=>{kind='chrome';await client.useProvider(provider());});};
 function web(){
  if(busy||!ready)return;
  chosen=true;root.clearInterval(discovery);
  let url=get('url').value.trim()||client.configuration.web_wallet_url;
  try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.origin===root.location.origin||parsed.username||parsed.password||parsed.search||parsed.hash)throw Error();const path=parsed.pathname.replace(/\/$/,'');parsed.pathname=path.endsWith('/wallet-connect')?path:path+'/wallet-connect';parsed.search='?embed=1';url=parsed.href;}catch{get('settings').open=true;get('url').focus();error='Your server administrator must configure a trusted HTTPS Castalia Wallet website on a separate origin.';render();return;}
  const epoch=++webEpoch;kind='web';client.disconnect();
  const host=get('embed');host.hidden=false;
  get('origin').textContent=new URL(url).origin;
  frame=root.document.createElement('iframe');frame.title='Castalia Wallet — unlock or import';
  frame.setAttribute('sandbox','allow-scripts allow-same-origin allow-downloads');
  frame.referrerPolicy='no-referrer';frame.src=url;get('frame').replaceChildren(frame);
  void run(async()=>{
   const {WebWalletProvider}=await import('/assets/credential-v2/web-wallet.mjs');
   if(epoch!==webEpoch||!frame)throw Error('wallet_connection_changed');
   const walletFrame=frame;
   const bridge=new WebWalletProvider({window:root,popup:walletFrame.contentWindow,url,mode:'embed',restore:true,
    onDispose:()=>{walletFrame.remove();if(frame===walletFrame){frame=null;host.hidden=true;}},
    onDisconnect:()=>client.disconnect()});
   pendingBridge=bridge;
   try{await bridge.ready();await client.useProvider(bridge);}catch(e){bridge.dispose();throw e;}
   finally{if(pendingBridge===bridge)pendingBridge=null;}
  });
 }
 get('web').onclick=web;
 const controller={get requestIdentity(){return client?.identity?{subject_public_key:client.identity.subject_public_key,installation_id:client.identity.installation_id}:null;},approveAccess:challenge=>client.approveAccess(challenge),get accessEndpoint(){return client?.configuration?.access_requests_endpoint??null;},get sessionKey(){return client?.sessionKey??'';},get preferenceSeed(){return client?.preferenceSeed??'';},get connected(){return client?.stage==='ready';},
  read:(path,options)=>client?client.read(path,options):Promise.reject(Object.assign(Error('wallet_read_access_required'),{status:401})),disconnect:()=>client?.disconnect()};
 render();
 void(async()=>{try{const {WalletReadClient}=await import('/assets/credential-v2/read-access.mjs');({AccessRequestClient}=await import('/assets/credential-v2/access-request.mjs'));client=new WalletReadClient({onChange:()=>{render();onChange(controller);}});await client.initialize();ready=true;
 const detect=()=>{if(chosen||busy||client.stage!=='choose')return false;if(!provider())return false;chosen=true;root.clearInterval(discovery);kind='chrome';void run(()=>client.useProvider(provider()));return true;};
 if(!detect()){let attempts=0;discovery=root.setInterval(()=>{if(detect()||chosen||++attempts>=20)root.clearInterval(discovery);},100);}}catch{error=messages.wallet_read_setup_required;}render();})();
 return controller;
}};
})(globalThis);
