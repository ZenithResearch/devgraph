/** Synthetic returning-user UI: no real Wallet, signing or installed authority. */
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {chromium} from '../../../packages/web/node_modules/playwright/index.mjs';
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const output=process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-returning-wallet-ui';
await mkdir(output,{recursive:true});
const markup=(await readFile(join(repo,'src/devgraph/frontend/wallet.py'),'utf8')).match(/r'''([\s\S]*?)'''/)[1];
const browser=await chromium.launch({headless:true});
const results=[];
try{
 for(const origin of ['https://graph.example','https://graph-example.tail123abc.ts.net:9443'])for(const width of [1280,375])for(const scenario of ['returning','reconnect','changed','first']){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(8000);
  await page.addInitScript(({origin,scenario})=>{
   let connected=['returning','changed'].includes(scenario),trusted=['returning','reconnect'].includes(scenario);
   window.fixtureCalls=[];
   window.devgraphWalletProvider={
    getCapabilities:async()=>['graph_access_v2','provider_profiles_v1','connection_v1','read_setup_status_v1'],
    proposeProviderProfile:async()=>{window.fixtureCalls.push('review');trusted=true;return{state:'approved'};},
    requestConnection:async()=>{window.fixtureCalls.push('connect');connected=true;return{state:'connected'};},
    readAccess:async input=>{
     window.fixtureCalls.push(input.operation);
     if(input.operation==='setup_status')return{schema:'castalia.read-setup-status.v1',connection_approved:connected,profile_approved:connected&&trusted};
     if(input.operation==='identity')return{subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32)};
     if(input.operation==='approve_graph_v2')return{accessId:'synthetic',enrollment:{}};
     if(input.operation==='accept_graph_v2')return{accessId:'synthetic',receiver:origin,expires_at:Math.floor(Date.now()/1000)+900};
     if(input.operation==='disconnect')return{state:'disconnected'};
     throw Error('unexpected fixture operation');
    }
   };
  },{origin,scenario});
  let enrollments=0;
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());assert.equal(url.origin,origin);
   const send=(value,type='application/json')=>route.fulfill({status:200,contentType:type,body:typeof value==='string'?value:JSON.stringify(value)});
   if(url.pathname==='/')return send('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;margin:16px;--aqua:#185c48;--canvas:white;--muted:#284c40}button{padding:12px}</style><link rel="stylesheet" href="/monitor/topology-assets/wallet-access.css">'+markup+'<script src="/monitor/topology-assets/wallet-access.js"></script><script>DevgraphWalletAccess.mount(document.querySelector("#wallet-read-connection"));</script>','text/html');
   const folder=url.pathname.startsWith('/monitor/topology-assets/')?'topology':url.pathname.startsWith('/assets/credential-v2/')?'credential-v2':null;
   if(folder)return send(await readFile(join(repo,'src/devgraph/frontend/static',folder,url.pathname.split('/').at(-1)),'utf8'),url.pathname.endsWith('.css')?'text/css':'text/javascript');
   if(url.pathname==='/monitor/wallet-access/v1')return send({schema:'devgraph.wallet-read-configuration.v1',endpoint:url.pathname,provider_profile:{schema:'castalia.provider-profile.v1',display_name:'Synthetic graph',origins:[origin],membership:null,presentations:[{issuer:'fixture',key_id:'key',public_key:'c'.repeat(64),audience:'devgraph',callers:[{kind:'browser',id:origin}]}]}});
   if(url.pathname.endsWith('/challenges'))return send({receiver:origin,expires_at:Math.floor(Date.now()/1000)+120});
   if(url.pathname.endsWith('/enrollments')){enrollments++;return send({schema:'devgraph.native-read-result.v4',credential:{}});}
   throw Error('unexpected fixture route');
  });
  await page.goto(origin);
  if(['reconnect','first'].includes(scenario))await page.locator('[data-wallet-advance]').filter({hasText:'Connect Wallet'}).click();
  if(['changed','first'].includes(scenario))await page.getByRole('button',{name:'Review server in Wallet',exact:true}).click();
  await page.getByRole('button',{name:'Allow read access in Wallet',exact:true}).waitFor();
  const calls=await page.evaluate(()=>window.fixtureCalls);
  const expected=scenario==='returning'?0:scenario==='first'?2:1;
  assert.equal(calls.filter(x=>['review','connect'].includes(x)).length,expected);
  assert.equal(calls.includes('approve_graph_v2'),false);assert.equal(enrollments,0);
  await page.getByRole('button',{name:'Allow read access in Wallet',exact:true}).click();
  try{await page.getByText('Wallet connected · read only',{exact:true}).waitFor();}
  catch(error){console.error({origin,width,scenario,status:await page.locator('[data-wallet-status]').textContent(),calls:await page.evaluate(()=>window.fixtureCalls),errors});throw error;}
  assert.equal(enrollments,1);assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(scenario==='returning')await page.screenshot({path:join(output,`returning-${origin === 'https://graph-example.tail123abc.ts.net:9443'?'tailnet':'public'}-${width}.png`),fullPage:true});
  results.push({origin,width,scenario,setup_clicks:expected,read_approval_clicks:1});
  await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'synthetic providers and APIs only; no actual Wallet consent',results,page_errors:[]},null,2));
 console.log('Returning Wallet walkthrough passed (16 scenarios).');
}finally{await browser.close();}
