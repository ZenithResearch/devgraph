/* Wallet-owned login/import, application-owned read transport. No key inputs. */
(function(root){
'use strict';
const messages={wallet_unlock_required:'Unlock Wallet, then continue.',wallet_connection_required:'Connect Wallet to this site, then continue.',wallet_upgrade_required:'Update Castalia Wallet to enable read access, or open your Web Wallet.',wallet_read_setup_required:'This server needs Wallet read access enabled by its administrator.',wallet_read_denied:'This Wallet has no current access to the requested work. Ask the server administrator to review your scope using the public connection details below, then choose Check access.',membership_selection_required:'Choose the membership you want to use.',membership_unavailable:'Add an active membership in Wallet, then connect again.',membership_required:'Add an active membership in Wallet, then connect again.',origin_not_approved:'Approve this site in Wallet before connecting.',user_denied:'Connection cancelled. Nothing was shared.',approval_denied:'Read access cancelled. Nothing was shared.',wallet_locked:'Unlock Wallet, then connect again.',read_access_required:'Connect Wallet again to read your shared work.',wallet_read_access_required:'Connect Wallet again to read your shared work.',read_access_expired:'Your read access expired. Connect Wallet to review fresh access.'};
root.DevgraphWalletAccess={mount(element,{onChange=()=>{},provider=()=>root.castaliaWallet}={}){
 const get=name=>element.querySelector(`[data-wallet-${name}]`);
 let client=null,busy=false,ready=false,error='',kind='';
 const buttons={access:'Check access',profile:'Review server in Wallet',connection:'Connect Wallet',approval:'Allow read access in Wallet',ready:'Connected'};
 const clearError=()=>{error='';};
 function render(){
  const stage=client?.stage??'loading',selected=['profile','connection','access','approval','ready'].includes(stage);
  get('chrome').hidden=selected||!provider();get('web').hidden=selected;get('import').hidden=selected;
  get('advance').hidden=!selected||stage==='ready';get('advance').disabled=busy;
  get('advance').textContent=buttons[stage]??'Continue in Wallet';
  get('disconnect').hidden=!selected;get('disconnect').disabled=busy;
  get('chrome').disabled=get('web').disabled=get('import').disabled=busy||!ready;
  get('membership-label').hidden=stage!=='approval'||client.memberships.length<2;
  if(stage==='approval'&&client.memberships.length>1){const select=get('membership');if(select.options.length!==client.memberships.length+1){select.replaceChildren(new Option('Choose membership',''),...client.memberships.map(p=>new Option(p.displayName,p.profileId)));}select.value=client.profileId??'';}
  get('access-details').hidden=stage!=='access'||!client?.connectionDetails;
  get('access-json').textContent=client?.connectionDetails?JSON.stringify(client.connectionDetails,null,2):'';
  get('copy').disabled=busy;
  get('status').textContent=error||(client?.notice==='read_scope_refreshed'?'Your access review expired. Permissions have been refreshed; choose Allow read access in Wallet to review them.':'')||({loading:'Preparing Wallet connection…',choose:'Connect your Wallet to view the work shared with you.',profile:'First, review this server’s public verification settings in Wallet.',access:'Checking the access assigned to this Wallet. If access has not been assigned, send the public details below to your server administrator.',connection:'Approve this site in Wallet. Your recovery file and password stay there.',approval:'Review the permitted work and expiry in Wallet.',ready:`Connected through ${kind==='chrome'?'Chrome Wallet':'Web Wallet'} · read only`}[stage]??'Ask the server administrator to enable Wallet read access.');
 }
 async function run(task){if(busy)return;busy=true;clearError();render();try{await task();}catch(e){error=messages[e?.code??e?.message]??'Could not connect. Check that Wallet is unlocked and your server is available.';}finally{busy=false;render();}}
 root.addEventListener('castalia:wallet:read-access-invalidated',()=>{if(kind==='chrome')client?.disconnect();});
 get('copy').onclick=()=>void run(async()=>{const details=client?.connectionDetails;if(!details)return;try{await root.navigator.clipboard.writeText(JSON.stringify(details,null,2));error='Public connection details copied. These details do not grant access.';}catch{error='Select and copy the public connection details below.';}});
 get('advance').onclick=()=>void run(()=>client.advance());
 get('disconnect').onclick=()=>{clearError();client.disconnect();render();};
 get('membership').onchange=event=>{if(client)client.profileId=event.target.value||undefined;};
 get('chrome').onclick=()=>void run(async()=>{kind='chrome';await client.useProvider(provider());});
 function web(restore){
  if(busy||!ready)return;
  let url=get('url').value.trim()||client.configuration.web_wallet_url;
  try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password||parsed.search||parsed.hash)throw Error();parsed.pathname=parsed.pathname.replace(/\/$/,'');if(!parsed.pathname.endsWith('/wallet-connect'))parsed.pathname+='/wallet-connect';url=parsed.href;}catch{get('settings').open=true;get('url').focus();error='Enter the HTTPS address of your trusted Castalia Web Wallet.';render();return;}
  // Open synchronously in this gesture. Never put a recovery file, password or
  // credential in the URL. The Wallet window owns choosing/unlocking the file.
  kind='web';const popup=root.open(url,'_blank','popup,width=520,height=760');
  if(!popup){error='Allow this Wallet window in your browser, then try again.';render();return;}
  void run(async()=>{const {WebWalletProvider}=await import('/assets/credential-v2/web-wallet.mjs');const bridge=new WebWalletProvider({window:root,popup,url,restore,onDisconnect:()=>client.disconnect()});try{await bridge.ready();await client.useProvider(bridge);}catch(e){bridge.dispose();throw e;}});
 }
 get('web').onclick=()=>web(false);get('import').onclick=()=>web(true);
 const controller={get sessionKey(){return client?.sessionKey??'';},get preferenceSeed(){return client?.preferenceSeed??'';},get connected(){return client?.stage==='ready';},
  read:(path,options)=>client?client.read(path,options):Promise.reject(Object.assign(Error('wallet_read_access_required'),{status:401})),disconnect:()=>client?.disconnect()};
 render();
 void(async()=>{try{const {WalletReadClient}=await import('/assets/credential-v2/read-access.mjs');client=new WalletReadClient({onChange:()=>{render();onChange(controller);}});await client.initialize();ready=true;}catch{error=messages.wallet_read_setup_required;}render();})();
 return controller;
}};
})(globalThis);
