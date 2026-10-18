/** Synthetic UI walkthrough only. No installed Wallet, credentials, grants or network. */
import{readFile,writeFile,mkdir}from'node:fs/promises';import{fileURLToPath}from'node:url';import{resolve,join}from'node:path';import assert from'node:assert/strict';
import{chromium}from'../../../packages/web/node_modules/playwright/index.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const output=resolve(process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-access-ui-evidence');await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.DEVGRAPH_BROWSER_EXECUTABLE?{executablePath:process.env.DEVGRAPH_BROWSER_EXECUTABLE}:{})});
try{
 const context=await browser.newContext({viewport:{width:1280,height:1000}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const publicKey='a'.repeat(64),device='b'.repeat(32),requestId='c'.repeat(32),digest='d'.repeat(64),actions=[];const now=Math.floor(Date.now()/1000);const results=new Map();let lostReview=false;
 const ceiling={schema:'devgraph.access-draft.v2',issuer:'fixture',receiver:'https://example.test',write_audience:'devgraph://receiver-local',not_before:now-10,not_after:now+7200,lifetime_seconds:300,read_grants:[{scope:'devgraph.work.read',operations:['get','list'],relationship_types:['parent'],work_kinds:['Todo','Task'],include_archived:false}],write_grants:[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task'}]},{operation:'devgraph.work.progress.set.v2',resources:[{kind:'Task'}]}]};
 await page.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());if(u.origin!=='https://example.test')return route.abort();
  const send=(value,type='application/json')=>route.fulfill({status:200,contentType:type,body:typeof value==='string'?value:JSON.stringify(value)});
  if(u.pathname==='/monitor/access/')return send((await readFile(join(root,'src/devgraph/frontend/static/access/index.html'),'utf8')).replace('__WALLET_CONNECTION__','<div id="wallet-read-connection"></div>'),'text/html');
  if(u.pathname==='/monitor/topology-assets/wallet-access.js')return send(`globalThis.DevgraphWalletAccess={mount(el,opts){queueMicrotask(()=>opts.onChange());return{requestIdentity:{subject_public_key:'${publicKey}',installation_id:'${device}'},approveAccess:async challenge=>({challenge,signature:'fixture',proof:{}})}}};`,'text/javascript');
  if(u.pathname.startsWith('/monitor/topology-assets/'))return send(await readFile(join(root,'src/devgraph/frontend/static/topology',u.pathname.split('/').at(-1)),'utf8'),u.pathname.endsWith('.css')?'text/css':'text/javascript');
  if(u.pathname.startsWith('/monitor/access-assets/'))return send(await readFile(join(root,'src/devgraph/frontend/static/access',u.pathname.split('/').at(-1)),'utf8'),u.pathname.endsWith('.css')?'text/css':'text/javascript');
  if(req.method()!=='POST')return route.fulfill({status:404,body:''});
  const body=req.postDataJSON();
  if(u.pathname.endsWith('/challenges'))return send({schema:'castalia.access-request-challenge.v1',receiver:u.origin,subject_public_key:publicKey,installation_id:device,details:{action:body.action}});
  const a=body.challenge.details.action;actions.push(a);const base={schema:'devgraph.access-operator-result.v1',action_id:a.action_id,authority_issued:false};
  if(a.action==='inbox')return send({...base,ceiling,ceiling_digest_sha256:digest,inbox:{total:1,next_cursor:null,items:[{request_id:requestId,subject_public_key:publicKey,installation_id:device,request_digest_sha256:digest,version:1,received_at:now,intent:null}]}});
  if(a.action==='review'){const result={...base,request_id:requestId,expected_version:1,request_digest_sha256:digest,review_id:a.action_id,selection:a.selection,selection_digest_sha256:digest,plans:{read:{},write:{}}};results.set(a.action_id,result);if(!lostReview){lostReview=true;return route.abort();}return send(result);}
  if(a.action==='status')return send({...base,original_action_id:a.original_action_id,status:'reconciled',result:results.get(a.original_action_id)});
  if(a.action==='approve')return send({...base,request_id:requestId,version:2,status:'approved_pending_activation'});
  throw Error('Unexpected test operation');
 });
 async function consent(){try{await page.locator('#confirm:not([hidden])').waitFor({timeout:5000});}catch{throw Error('Consent not ready: '+await page.locator('#status').textContent()+'; '+errors.join('; '));}await page.locator('#confirm').click();}
 await page.goto('https://example.test/monitor/access/');await page.getByRole('button',{name:'Review inbox',exact:true}).click();await consent();await page.getByRole('button',{name:/Member aaaa/}).click();
 await page.locator('#mode').selectOption('both');await page.getByRole('button',{name:'Select all permitted writes'}).click();await page.locator('#scope').selectOption('objects');await page.locator('#objects').fill('Task/one');await page.getByRole('button',{name:'Review selection',exact:true}).click();await consent();await page.getByRole('status').filter({hasText:'The result is unknown'}).waitFor();await page.reload();await page.locator('#recover').click();await consent();await page.locator('#selection-review').waitFor({state:'visible'});assert.equal(actions.filter(a=>a.action==='review').length,1);assert.equal(await page.locator('#member').textContent(),publicKey);assert.equal(await page.locator('#edit').textContent(),'Reload inbox to edit');
 for(const theme of['light','dark','aqua','system']){await page.locator('#theme').selectOption(theme);assert.equal(await page.locator('html').getAttribute('data-theme-preference'),theme);}
 await page.locator('#theme').selectOption('light');await page.screenshot({path:join(output,'grant-review-wide.png'),fullPage:true});
 await page.setViewportSize({width:375,height:812});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:join(output,'grant-review-narrow.png'),fullPage:true});
 await page.getByRole('button',{name:'Approve this selection',exact:true}).click();await consent();await page.getByRole('status').filter({hasText:'Selection approved. Issuer activation is still required'}).waitFor();
 assert.equal(actions.filter(a=>a.action==='approve').length,1);assert.equal(actions.find(a=>a.action==='approve').selection.write.write_grants[0].resources[0].ids[0],'one');assert.deepEqual(errors,[]);
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'synthetic UI only; no real Wallet approval or grant activation',themes:4,widths:[1280,375],narrow_overflow:false,decision_submissions:1,lost_review_recovered_after_reload:true,page_errors:errors},null,2));console.log('Synthetic operator grant walkthrough passed.');await context.close();
}finally{await browser.close();}
