/** Synthetic handoff UI only: no installed Wallet, authority or real consent. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir}from'node:fs/promises';
import {fileURLToPath}from'node:url';
import {join,resolve}from'node:path';
import {chromium}from'../../../packages/web/node_modules/playwright/index.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const output=resolve(process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-read-handoff-ui');await mkdir(output,{recursive:true});
const source=await readFile(join(root,'src/devgraph/frontend/wallet.py'),'utf8');
const currentMarkup=source.match(/r'''([\s\S]*?)'''/)[1];
const markup=process.env.DEVGRAPH_LEGACY_REQUEST_MARKUP==='1'?currentMarkup.replace('<p data-wallet-request-status role="status" aria-live="polite" hidden></p>',''):currentMarkup;
const origins=['https://graph.example','https://graph-example.tail123abc.ts.net:9443'],holder='a'.repeat(64),installation='b'.repeat(32);
const browser=await chromium.launch({headless:true});
try{
 for(const providerMode of ['legacy','host'])for(const origin of origins)for(const width of [1280,375]){
  const label=providerMode+'-'+(origin === 'https://graph-example.tail123abc.ts.net:9443'?'tailnet-':'public-');
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
  let now=Math.floor(Date.now()/1000),challenges=0,enrollments=0,presentations=0,setupAttempts=0,denyChallenges=false,requestResult='pending',requestActions=[];
  await page.clock.install({time:new Date(now*1000)});
  await page.addInitScript(({origin,holder,installation,providerMode})=>{
   window.fixtureCalls=[];window.fixtureMode='pending';
   const fixtureWallet={getCapabilities:async()=>[providerMode==='host'?'graph_access_v2':'read_access_v1','provider_profiles_v1','connection_v1','access_request_v1'],proposeProviderProfile:async()=>({state:'approved'}),requestConnection:async()=>({state:'connected'}),readAccess:async input=>{
    window.fixtureCalls.push(input.operation);const operation=input.operation.replace('_graph_v2','');
    if(operation==='identity')return{subject_public_key:holder,installation_id:installation,...(providerMode==='legacy'?{memberships:[{profileId:'fixture'}]}:{})};
    if(operation==='request')return{approval:{challenge:input.challenge,proof:{},signature:'x'.repeat(86)}};
    if(operation==='approve'){if(window.fixtureMode==='locked')throw Error('wallet_locked');return new Promise(resolve=>{window.fixtureApprove=()=>resolve({accessId:'test-only-access',enrollment:{challenge:{},signature:'synthetic',proof:{}}});});}
    if(operation==='accept')return{accessId:'test-only-access',expires_at:Math.floor(Date.now()/1000)+300,receiver:origin};
    if(operation==='present')return{credential:{},call:input.call,proof:{}};
    return{state:'disconnected'};
   }};
   if(providerMode==='host'){window.devgraphWalletProvider=fixtureWallet;window.castaliaWallet={getCapabilities:async()=>{throw Error('legacy provider must not replace the host provider');}};}else window.castaliaWallet=fixtureWallet;
  },{origin,holder,installation,providerMode});
  await page.route('**/*',async route=>{
   const req=route.request(),u=new URL(req.url());if(u.origin!==origin)return route.abort();
   const send=(v,type='application/json')=>route.fulfill({status:200,contentType:type,body:typeof v==='string'?v:JSON.stringify(v)});
   if(u.pathname==='/')return send('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/monitor/topology-assets/wallet-access.css"><style>body{font:16px system-ui;margin:16px;--aqua:#185c48;--canvas:white;--muted:#284c40}button{padding:12px;border-radius:8px}pre{white-space:pre-wrap}</style>'+markup+'<button id="fixture-consent">Approve synthetic test fixture</button><p id="read-proof" role="status"></p><script src="/monitor/topology-assets/wallet-access.js"></script><script>let last;DevgraphWalletAccess.mount(document.querySelector("#wallet-read-connection"),{onChange:c=>{if(!c.connected)document.querySelector("#read-proof").textContent="";if(c.connected&&c.sessionKey!==last){last=c.sessionKey;c.read("/todos/v2?limit=1").then(r=>document.querySelector("#read-proof").textContent="Verified scoped read: "+r.total)}}});document.querySelector("#fixture-consent").onclick=()=>window.fixtureApprove?.();</script>','text/html');
   const asset=u.pathname.startsWith('/monitor/topology-assets/')?'static/topology':u.pathname.startsWith('/assets/credential-v2/')?'static/credential-v2':null;
   if(asset)return send(await readFile(join(root,'src/devgraph/frontend',asset,u.pathname.split('/').at(-1)),'utf8'),u.pathname.endsWith('.css')?'text/css':'text/javascript');
   if(u.pathname==='/monitor/wallet-access/v1'){if(++setupAttempts===1)return route.fulfill({status:503,body:'temporarily unavailable'});return send({schema:'devgraph.wallet-read-configuration.v1',endpoint:u.pathname,web_wallet_url:null,access_requests_endpoint:'/devgraph/access-requests/v2',provider_profile:{schema:'castalia.provider-profile.v1',display_name:'Synthetic graph',origins:[origin],membership:null,presentations:[{issuer:'fixture',key_id:'key',public_key:'c'.repeat(64),audience:'devgraph',callers:[{kind:'browser',id:origin}]}]}});}
   if(u.pathname==='/devgraph/access-requests/v2/challenges'){const action=req.postDataJSON().request;return send({schema:'castalia.access-request-challenge.v1',receiver:origin,subject_public_key:holder,installation_id:installation,expires_at:now+120,details:action});}
   if(u.pathname==='/devgraph/access-requests/v2/actions'){const approval=req.postDataJSON();requestActions.push(approval.challenge.details);return send({schema:'devgraph.access-request-result.v1',request_id:approval.challenge.details.request_id,status:requestResult,authority_issued:false});}
   if(u.pathname.endsWith('/challenges')){if(denyChallenges)return route.fulfill({status:401,contentType:'application/json',body:'{}'});challenges++;return send({receiver:origin,expires_at:now+(challenges===1?6:120)});}
   if(u.pathname.endsWith('/enrollments')){enrollments++;return send({schema:'devgraph.native-read-result.v3',credential:{}});}
   if(u.pathname.endsWith('/requests')){presentations++;return send({items:[{kind:'Todo',id:'visible'}],total:2});}
   return route.fulfill({status:404,body:''});
  });
  await page.goto(origin);await page.getByRole('button',{name:'Retry connection setup',exact:true}).click();await page.getByRole('button',{name:'Review server in Wallet',exact:true}).waitFor();assert.equal(setupAttempts,2);assert.equal(await page.locator('[data-wallet-server]').textContent(),origin);await page.getByRole('button',{name:'Review server in Wallet',exact:true}).click();await page.getByRole('button',{name:'Connect Wallet',exact:true}).click();
  now+=2;await page.clock.fastForward(2000);await page.getByRole('button',{name:'Refresh read review',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.fixtureCalls.filter(x=>x==='approve'||x==='approve_graph_v2').length),0);assert.equal(enrollments,0);
  await page.screenshot({path:join(output,label+'expired-'+width+'.png'),fullPage:true});
  await page.getByRole('button',{name:'Refresh read review',exact:true}).click();await page.getByRole('button',{name:'Allow read access in Wallet',exact:true}).waitFor();assert.equal(challenges,2);assert.equal(enrollments,0);
  // A pending access-request result must not conceal the live read-connection state.
  await page.locator('[data-wallet-request]').click();await page.locator('[data-wallet-request]').click();
  await page.locator('[data-wallet-request-status]').filter({hasText:'Last access request update: Request sent for operator review.'}).waitFor();
  assert.match(await page.locator('[data-wallet-status]').textContent(),/Permissions have been refreshed/);
  await page.getByRole('button',{name:'Allow read access in Wallet',exact:true}).click();try{await page.getByRole('button',{name:'Opening Wallet…',exact:true}).waitFor();}catch(e){console.error(JSON.stringify({status:await page.locator('[data-wallet-status]').textContent(),buttons:await page.locator('.wallet-connection-actions').textContent(),calls:await page.evaluate(()=>window.fixtureCalls),errors}));await page.screenshot({path:join(output,label+'failure-'+width+'.png'),fullPage:true});throw e;}assert.equal(await page.locator('#read-proof').textContent(),'');assert.equal(enrollments,0);
  await page.screenshot({path:join(output,label+'opening-'+width+'.png'),fullPage:true});
  await page.locator('#fixture-consent').click();await page.getByText('Wallet connected · read only',{exact:true}).waitFor();await page.getByText('Verified scoped read: 2',{exact:true}).waitFor();assert.equal(enrollments,1);assert.equal(presentations,1);assert.equal(await page.locator('[data-wallet-step][data-state=complete]').count(),3);assert.equal(await page.locator('[aria-current=step]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
  await page.screenshot({path:join(output,label+'connected-'+width+'.png'),fullPage:true});
  assert.match(await page.locator('[data-wallet-request-status]').textContent(),/^Last access request update: Request sent for operator review/);
  if(providerMode==='host')await page.evaluate(()=>window.dispatchEvent(new CustomEvent('devgraph:wallet:invalidated')));else await page.locator('[data-wallet-disconnect]').click();
  assert.equal(await page.locator('[data-wallet-status]').textContent(),'Connect your Wallet to request access or view the work shared with you.');
  assert.equal(await page.locator('[data-wallet-request-status]').isVisible(),false);
  // A holder without a grant can request access and recover status without duplicating it.
  denyChallenges=true;
  await page.locator('[data-wallet-chrome]').click();await page.getByRole('button',{name:'Review server in Wallet',exact:true}).click();await page.getByRole('button',{name:'Connect Wallet',exact:true}).click();
  await page.getByText('No current read access was found. Choose Request access to send a request for the service owner to review.',{exact:true}).waitFor();
  await page.locator('[data-wallet-request]').click();await page.locator('[data-wallet-request]').click();
  await page.getByText('Your request is waiting for the service owner. Choose Check request status to follow its decision.',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-wallet-step="2"]').getAttribute('aria-current'),'step');assert.equal(enrollments,1);
  requestResult='unknown';await page.locator('[data-wallet-request]').click();await page.locator('[data-wallet-request]').click();
  await page.getByText('The original request outcome is unknown. Choose Check request status to recover it; no new request will be sent.',{exact:true}).waitFor();
  requestResult='denied';await page.locator('[data-wallet-request]').click();await page.locator('[data-wallet-request]').click();
  await page.locator('[data-wallet-request-status]').filter({hasText:'Your access request was declined.'}).waitFor();
  assert.equal(await page.locator('[data-wallet-new-request]').isVisible(),true);assert.equal(enrollments,1);assert.deepEqual(errors,[]);assert.equal(requestActions.filter(a=>a.action==='submit').length,1);assert.equal(new Set(requestActions.map(a=>a.request_id)).size,1);
  await page.screenshot({path:join(output,label+'request-declined-'+width+'.png'),fullPage:true});
  await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'synthetic UI; no actual Wallet approval or installed grant',origins,provider_modes:['legacy extension','generic host, graph v2 only'],widths:[1280,375],setup_retry:true,pending_unknown_denied_request_states:true,expired_review_label:true,refresh_does_not_call_wallet:true,opening_does_not_announce_connected:true,connection_requires_accepted_credential:true,automatic_scoped_read_after_accept:true,request_history_does_not_mask_connection:true,request_history_hidden_after_disconnect:true,page_errors:[]},null,2));
 console.log('Synthetic read handoff walkthrough passed.');
}finally{await browser.close();}
