/** Actual graph page and staged public files; synthetic Wallet, no live authority. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {chromium} from '../../../packages/web/node_modules/playwright/index.mjs';

const shell=resolve(process.env.DEVGRAPH_MONITOR_SHELL??'/private/tmp/devgraph-wallet-graph-shell-20261023');
const output=resolve(process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-wallet-graph-page-ui');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const origins=['https://graph.example','https://graph-example.tail123abc.ts.net:9443'];
try{
 for(const providerMode of ['legacy','host','late-host','manual-extension'])for(const origin of origins)for(const width of [1280,375]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[],actions=[];
  let configured=false,grantsRequested=0,websiteRequests=0;
  page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
  await page.clock.install();
  await page.addInitScript(providerMode=>{
   window.fixtureCalls=[];
   window.fixtureWallet={
    getCapabilities:async()=>[providerMode==='legacy'?'read_access_v1':'graph_access_v2','provider_profiles_v1','connection_v1','access_request_v1'],
    proposeProviderProfile:async()=>({state:'approved'}),requestConnection:async()=>({state:'connected'}),
    readAccess:async input=>{
     window.fixtureCalls.push(input.operation);
     if(input.operation==='identity')return{subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),...(providerMode==='legacy'?{memberships:[{profileId:'fixture'}]}:{})};
     if(input.operation==='request')return{approval:{challenge:input.challenge,proof:{},signature:'x'.repeat(86)}};
     throw Error('Unexpected credential operation in setup/request test');
    }
   };
   if(providerMode==='legacy')window.castaliaWallet=window.fixtureWallet;
   if(providerMode==='host')window.devgraphWalletProvider=window.fixtureWallet;
  },providerMode);
  await context.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin!==origin){websiteRequests++;return route.abort();}
   const send=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.pathname==='/monitor/wallet-access/v1'){
    if(!configured)return send({error:'endpoint_wallet_setup_required'},503);
    return send({schema:'devgraph.wallet-read-configuration.v1',endpoint:url.pathname,access_requests_endpoint:'/devgraph/access-requests/v2',provider_profile:{schema:'castalia.provider-profile.v1',display_name:'Synthetic graph',origins:[origin],membership:null,presentations:[{issuer:'fixture',key_id:'key',public_key:'c'.repeat(64),audience:'devgraph',callers:[{kind:'browser',id:origin}]}]}});
   }
   if(url.pathname==='/monitor/wallet-access/v1/challenges')return send({error:'access_denied'},401);
   if(url.pathname==='/monitor/wallet-access/v1/enrollments'){grantsRequested++;return send({},403);}
   if(url.pathname==='/devgraph/access-requests/v2/challenges')return send({schema:'castalia.access-request-challenge.v1',receiver:origin,subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),expires_at:Math.floor(Date.now()/1000)+120,details:req.postDataJSON().request});
   if(url.pathname==='/devgraph/access-requests/v2/actions'){
    const action=req.postDataJSON().challenge.details;actions.push(action);
    return send({schema:'devgraph.access-request-result.v1',request_id:action.request_id,status:'pending',authority_issued:false});
   }
   if(req.method()!=='GET')return send({},404);
   let pathname=url.pathname;
   if(['/', '/monitor', '/monitor/','/monitor/kanban/'].includes(pathname))pathname=pathname.replace(/\/$/,'')+'/index.html';
   try{
    const file=resolve(shell,'.'+pathname);assert.ok(file.startsWith(shell+'/'));
    const body=await readFile(file);
    return route.fulfill({contentType:file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'text/javascript',body});
   }catch{return send({},404);}
  });
  await page.goto(origin+'/#overview');
  const connection=page.locator('#connection-settings');
  assert.match(await connection.locator('summary').first().textContent(),/Connect to your graph with Wallet/);
  assert.equal(await connection.locator('#wallet-read-connection').count(),1);
  assert.equal(await page.locator('input[type=password],#token,#credential').count(),0);
  assert.equal(await page.getByPlaceholder('Paste a local devgraph.read credential').count(),0);
  await connection.getByRole('button',{name:'Retry connection setup',exact:true}).waitFor();
  assert.match(await connection.locator('[data-wallet-status]').textContent(),/Wallet access is not enabled for this server address yet/);
  assert.equal(await connection.locator('[data-wallet-chrome]').isEnabled(),false);
  assert.equal(await page.locator('[data-wallet-request]').isVisible(),false);
  assert.equal(await connection.locator('[data-wallet-web]').isVisible(),false);
  await connection.getByText('Wallet connection help',{exact:true}).click();
  assert.equal(await connection.locator('[data-wallet-website-settings]').isVisible(),false);
  assert.equal(await connection.locator('[data-wallet-url]').isVisible(),false);
  assert.match(await connection.locator('[data-wallet-provider-status]').textContent(),['late-host','manual-extension'].includes(providerMode)?/No Wallet detected/:/Wallet provider found/);
  const label=providerMode+'-'+(origin === 'https://graph-example.tail123abc.ts.net:9443'?'tailnet':'public');
  await page.screenshot({path:join(output,`${label}-setup-${width}.png`),fullPage:true});
  configured=true;await connection.getByRole('button',{name:'Retry connection setup',exact:true}).click();
  if(providerMode==='late-host'){await connection.getByText('Connect your Wallet to request access or view the work shared with you.',{exact:true}).waitFor();await page.clock.fastForward(3000);await page.evaluate(()=>{window.devgraphWalletProvider=window.fixtureWallet;window.dispatchEvent(new CustomEvent('devgraph:wallet-provider-ready'));});}
  if(providerMode==='manual-extension'){await connection.getByRole('button',{name:'Check for Wallet',exact:true}).waitFor();await page.clock.fastForward(3000);await connection.getByRole('button',{name:'Check for Wallet',exact:true}).click();assert.match(await connection.locator('[data-wallet-status]').textContent(),/No Wallet extension is available/);assert.deepEqual(await page.evaluate(()=>window.fixtureCalls),[]);assert.equal(await page.getByRole('button',{name:/Wallet website/i}).count(),0);assert.equal(await page.locator('[data-wallet-url],iframe').count(),0);await page.evaluate(()=>{window.castaliaWallet=window.fixtureWallet;});await connection.getByRole('button',{name:'Check for Wallet',exact:true}).click();}
  await connection.getByRole('button',{name:'Review server in Wallet',exact:true}).click();
  assert.equal(await connection.locator('[data-wallet-web]').isVisible(),false);assert.equal(await connection.locator('[data-wallet-url]').isVisible(),false);
  await connection.getByRole('button',{name:'Connect Wallet',exact:true}).click();
  await connection.getByText('No current read access was found. Choose Request access to send a request for the service owner to review.',{exact:true}).waitFor();
  await connection.locator('[data-wallet-request]').click();await connection.locator('[data-wallet-request]').click();
  await connection.getByText('Your request is waiting for the service owner. Choose Check request status to follow its decision.',{exact:true}).waitFor();
  assert.equal(await page.locator('#connection-label').textContent(),'Disconnected');
  assert.equal(grantsRequested,0);assert.equal(websiteRequests,0);assert.equal(actions.length,1);assert.equal(actions[0].action,'submit');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
  await page.screenshot({path:join(output,`${label}-pending-${width}.png`),fullPage:true});
  await connection.getByRole('button',{name:'Disconnect',exact:true}).click();
  assert.equal(await connection.locator('[data-wallet-request-status]').isVisible(),false);
  await page.goto(origin+'/monitor/kanban/');if(['late-host','manual-extension'].includes(providerMode))await page.evaluate(()=>{window.devgraphWalletProvider=window.fixtureWallet;window.dispatchEvent(new CustomEvent('devgraph:wallet-provider-ready'));});await page.locator('[data-wallet-advance]').waitFor({state:'visible'});
  assert.equal(await page.locator('input[type=password],#credential').count(),0);
  assert.deepEqual(errors,[]);await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'Actual graph-page markup and exported assets; synthetic Wallet/API; no live credentials',origins,provider_modes:['legacy extension','generic host','late generic host','manual detection after discovery timeout'],widths:[1280,375],wallet_inside_connect_graph:true,pasted_credential_removed:true,setup_missing_fails_closed:true,extension_needs_no_website:true,website_controls_hidden_with_provider:true,pending_not_connected:true,board_uses_wallet:true,page_errors:[]},null,2));
 console.log('Actual graph connection panel walkthrough passed.');
}finally{await browser.close();}
