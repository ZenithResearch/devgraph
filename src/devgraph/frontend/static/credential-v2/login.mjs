import {WalletLoginClient} from './session-login.mjs';
import {loginReturnPath} from './session-store.mjs';

const button=document.querySelector('#wallet-login'),status=document.querySelector('#login-status'),failure=document.querySelector('#login-error');
const destination=loginReturnPath(new URL(location.href).searchParams.get('return'));
document.querySelector('#login-server').textContent=location.origin;
const messages={
 wallet_update_required:'Update your Wallet extension to use popup sign in, then reload this page.',
 wallet_provider_missing:'A compatible Wallet was not detected. Enable your Wallet extension for this site, then try again.',
 wallet_not_setup:'Set up or import your Wallet in its extension, then return here to sign in.',
 approval_unavailable:'Wallet could not open its sign-in window. Allow the extension to run on this site, then try again.',
 approval_required:'Click Sign in with Wallet again to open its review window.',
 session_login_cancelled:'Sign in was cancelled. No new browser session was started.',
 user_denied:'Sign in was cancelled. No new browser session was started.',
 session_login_busy:'A Wallet sign-in window is already open. Finish or close that window first.',
 session_login_expired:'The sign-in review expired. Sign in again to review a new session.',
 session_login_context_changed:'The Wallet or sign-in window changed. Sign in again to review a fresh session.',
 issuer_setup_required:'Review this server’s verification settings in the Wallet window before continuing.',
 read_approval_expired:'The sign-in review expired. Sign in again to review a new session.',
 session_storage_unavailable:'This browser could not keep the temporary session. Enable site storage, then try again.',
 read_access_expired:'Your temporary session expired. Sign in to review a new one.',
 read_access_revoked:'This session is no longer authorized. Sign in to check your current access.',
 holder_session_outcome_unknown:'The original sign-in result is uncertain. Check its status before starting another session.',
 holder_session_setup_invalid:'This server and Wallet have incompatible session settings. The service owner needs to check the setup.',
 invalid_holder_session:'The session could not be verified. No access was opened.',
 monitor_arena_context_required:'DevGraph Monitor requires Work listing and Arena listing for the work you can read. Ask the service owner to include Arena access in your grant, then sign in again.',
 session_login_access_pending:'Your access request is awaiting review. Sign in again to check its status in Wallet.',
 grant_not_saved:'No reusable grant is saved yet. Continue in Wallet to check your approved access.',
 endpoint_wallet_setup_required:'Wallet access is not configured for this server address. Contact the service owner.',
 browser_session_changed:'The browser session changed in another tab. Reload to check the current session.'
};
let busy=true,preparing=null,discovery=null;const provider=()=>globalThis.devgraphWalletProvider??globalThis.castaliaWallet;
const client=new WalletLoginClient({onChange:render});
function render(){
 const recovery=client.stage==='session_recovery';button.disabled=busy;
 button.textContent=busy?({opening_wallet:'Opening Wallet…',awaiting_consent:'Continue in Wallet…'}[client.stage]??'Preparing sign in…'):recovery?'Check sign-in status':client.stage==='login_ready'?'Sign in with Wallet':'Retry sign-in setup';
 status.textContent=client.stage==='opening_wallet'||client.stage==='awaiting_consent'?'Continue in the Wallet window. You can unlock, check access and approve your session there.':recovery?'Checking the original result will not request another credential.':busy?'Checking session setup…':'Your Wallet will show the exact permissions and expiry before you approve.';
}
function report(error){failure.textContent=messages[error?.code??error?.message]??'Sign in could not finish. Check your connection and try again.';failure.hidden=false;}
async function prepare(){
 if(preparing)return preparing;
 preparing=(async()=>{const wallet=provider();if(!wallet)throw Error('wallet_provider_missing');await client.prepareLogin(wallet);})();
 try{await preparing;}finally{preparing=null;}
}
button.addEventListener('click',()=>{
 if(busy)return;failure.hidden=true;busy=true;
 // login() opens the extension window before its first await. Do not add an
 // async storage/setup step between this gesture and the provider call.
 let pending;
 if(client.stage==='session_recovery')pending=client.reconcileLogin();
 else if(client.stage==='login_ready')pending=client.login();
 else{busy=false;void initialize(false);return;}
 render();void pending.then(()=>{if(client.stage==='ready')location.replace(destination);else if(client.notice)report(Error(client.notice));}).catch(async error=>{
  report(error);
  // A lost reply can already have issued the original credential. Try one
  // authenticated status lookup, never a second popup or issuance attempt.
  if(client.stage==='session_recovery')try{
   if(await client.reconcileLogin())location.replace(destination);
   else if(client.notice)report(Error(client.notice));
  }catch{}
 }).finally(async()=>{
  if(client.stage!=='ready'&&client.stage!=='session_recovery')try{await prepare();}catch(error){report(error);}
  busy=false;render();
 });
});
async function initialize(resume=true){
 busy=true;render();
 try{
  if(resume&&await client.resumeLogin()){location.replace(destination);return;}
  if(client.stage==='session_recovery'){if(client.notice)report(Error(client.notice));return;}
  if(client.notice)report(Error(client.notice));
  await prepare();
 }catch(error){report(error);if((error?.code??error?.message)==='wallet_provider_missing')watchProvider();}finally{busy=false;render();}
}
function watchProvider(){
 clearInterval(discovery);let attempts=0;
 discovery=setInterval(()=>{if(provider()){clearInterval(discovery);if(!busy){failure.hidden=true;void initialize(false);}}else if(++attempts>=30)clearInterval(discovery);},200);
}
globalThis.addEventListener('devgraph:wallet-provider-ready',()=>{if(!busy&&client.stage!=='login_ready'&&client.stage!=='session_recovery')void initialize(false);});
globalThis.addEventListener('pagehide',()=>{clearInterval(discovery);client.disconnect();});
globalThis.addEventListener('pageshow',event=>{if(event.persisted)void initialize();});
void initialize();
