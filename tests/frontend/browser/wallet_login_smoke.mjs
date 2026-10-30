/** Real shells, IndexedDB/CryptoKey and navigation. Synthetic Wallet/issuer only. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {generateKeyPairSync,createHash,createPublicKey,sign,verify,randomBytes} from 'node:crypto';
const require=createRequire(process.env.DEVGRAPH_PLAYWRIGHT_PACKAGE??new URL('../../../packages/web/package.json',import.meta.url));
const {chromium}=require('playwright-core');
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const output=process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-popup-login-ui';await mkdir(output,{recursive:true});
const fixture=JSON.parse(execFileSync(process.env.DEVGRAPH_PYTHON??'python3',['tests/frontend/browser/login_shell_fixture.py'],{cwd:repo,env:{...process.env,PYTHONPATH:join(repo,'src')},encoding:'utf8'}));
const canonical=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
const transcript=(domain,v)=>Buffer.from(domain+'\0'+canonical(v));
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
const {privateKey:key,publicKey}=generateKeyPairSync('ed25519');
const pin={issuer:'synthetic',key_id:'key',public_key:publicKey.export({format:'der',type:'spki'}).subarray(-32).toString('hex'),service:'service:fixture'};
const browser=await chromium.launch({headless:true,executablePath:process.env.DEVGRAPH_CHROMIUM_EXECUTABLE}),results=[];
try{
 for(const width of [1280,375])for(const tailnet of [false,true])for(const scenario of ['normal','lost-reply','transient-status','revoked','expired','revoked-manual','expired-manual']){
  const terminalState=scenario.startsWith('revoked')?'revoked':scenario.startsWith('expired')?'expired':null,manual=scenario.endsWith('-manual');
  const origin=tailnet?'https://graph-example.tail123abc.ts.net:9443':'https://graph.example';
  const context=await browser.newContext({viewport:{width,height:950}}),page=await context.newPage(),errors=[],calls=[];
  let challenge,credential,popups=0,reads=0,statuses=0,revoked=false;const nonces=new Set();
  page.setDefaultTimeout(10000);context.on('page',p=>{void p.opener().then(opener=>{if(opener===page)popups++;});});page.on('pageerror',e=>errors.push(e.message));
  await context.addInitScript(()=>{
   if(location.pathname==='/fixture/wallet')return;
   const post=async(path,body)=>(await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
   window.devgraphWalletProvider={getCapabilities:async()=>['session_login_v1'],readAccess:input=>{
    if(input.operation==='open_session_login'){
     window.fixturePopup=window.open('/fixture/wallet','wallet','width=420,height=600');
     if(!window.fixturePopup)throw Error('approval_unavailable');
     return post('/fixture/challenge',input).then(challenge=>({schema:'castalia.session-login-prepared.v1',loginId:input.loginId,challenge}));
    }
    if(input.operation==='complete_session_login')return new Promise((resolve,reject)=>{window.fixtureApprove=()=>post('/fixture/approve',input).then(resolve,reject);});
    if(input.operation==='cancel_session_login')return Promise.resolve({schema:'castalia.session-login-cancelled.v1',dispatched:false});
    throw Error('unexpected_wallet_operation');
   }};
  });
  const profile={schema:'castalia.provider-profile.v1',display_name:'Synthetic graph',origins:[origin],membership:null,presentations:[{issuer:pin.issuer,key_id:pin.key_id,public_key:pin.public_key,audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin);
   const send=(value,type='application/json',status=200)=>route.fulfill({status,contentType:type,body:typeof value==='string'?value:JSON.stringify(value)});
   if(fixture.shells[url.pathname])return route.fulfill({status:200,...fixture.shells[url.pathname]});
   if(url.pathname==='/fixture/wallet')return send('<h1>Synthetic Wallet review</h1><button id="approve">Approve temporary session</button><script>document.querySelector("button").onclick=()=>{opener.fixtureApprove();close();}</script>','text/html');
   const folder=url.pathname.startsWith('/monitor/topology-assets/')?'topology':url.pathname.startsWith('/assets/credential-v2/')?'credential-v2':url.pathname.startsWith('/assets/kanban/')||url.pathname.startsWith('/monitor/kanban-assets/')?'kanban':null;
   if(folder)return send(await readFile(join(repo,'src/devgraph/frontend/static',folder,url.pathname.split('/').at(-1)),'utf8'),url.pathname.endsWith('.css')?'text/css':'text/javascript');
   if(url.pathname==='/monitor/wallet-access/v1')return send({schema:'devgraph.wallet-read-configuration.v1',endpoint:url.pathname,provider_profile:profile,holder_grants:{schema:'secs.holder-grant.v2',service:pin.service,endpoint:url.pathname+'/grants'}});
   if(url.pathname==='/holder-sessions/v1/capabilities')return send({schema:'secs.holder-session-capabilities.v1',supported:true,profile:'secs.holder-session.v1',issuer_pin:pin,audience:'devgraph',grant_schemas:['secs.holder-grant.v2'],credential_schema:'secs.holder-session-credential.v1',presentation_schema:'secs.holder-session-presentation.v1',read_only:true,max_ttl_seconds:900,review_ttl_seconds:120,proof_max_ttl_seconds:60,challenge_path:'/holder-sessions/v1/challenges',exchange_path:'/holder-sessions/v1/exchange',status_path:'/holder-sessions/v1/status'});
   if(url.pathname==='/fixture/challenge'){
    calls.push('open');const input=request.postDataJSON(),now=Math.floor(Date.now()/1000);
    challenge={schema:'secs.holder-session-challenge.v1',challenge_id:randomBytes(16).toString('hex'),request_id:randomBytes(16).toString('hex'),nonce:randomBytes(32).toString('hex'),issuer:pin.issuer,key_id:pin.key_id,service:pin.service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'2'.repeat(64),holder_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),session_public_key:input.session_public_key,session_installation_id:input.session_installation_id,permissions:[{operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph'}],issued_at:now,review_deadline:now+120,expires_at:now+900};return send(challenge);
   }
   const child=()=>createPublicKey({key:Buffer.from('302a300506032b6570032100'+challenge.session_public_key,'hex'),format:'der',type:'spki'});
   if(url.pathname==='/fixture/approve'){
    calls.push('complete');const input=request.postDataJSON();assert.deepEqual(input.challenge,challenge);assert.ok(verify(null,transcript('secs.holder-session-possession.v1/signature',challenge),child(),Buffer.from(input.session_signature,'hex')));
    if(terminalState)return route.abort('failed');
    const claims={schema:'secs.holder-session-credential.v1',credential_id:randomBytes(16).toString('hex'),challenge_digest:hash(challenge),challenge};credential={claims,signature:sign(null,transcript('secs.holder-session-credential.v1/signature',claims),key).toString('hex')};
    if(scenario==='lost-reply')return route.abort('failed');
    return send({schema:'castalia.holder-session-opened.v1',credential});
   }
   if(url.pathname==='/holder-sessions/v1/status'){
    statuses++;if(scenario==='transient-status'&&statuses===1)return send({error:'temporarily_unavailable'},'application/json',503);const input=request.postDataJSON(),proof={...input};delete proof.signature;assert.ok(verify(null,transcript('secs.holder-session-status.v1/signature',proof),child(),Buffer.from(input.signature,'hex')));
    const state=terminalState?(manual&&statuses===1?'pending':terminalState):revoked?'revoked':'issued';
    return send({schema:'secs.holder-session-result.v1',request_id:challenge.request_id,challenge_id:challenge.challenge_id,state,credential:state==='issued'?credential:null,expires_at:challenge.expires_at});
   }
   if(url.pathname==='/monitor/wallet-access/v1/requests'){
    const input=request.postDataJSON(),proof={...input.proof};delete proof.signature;assert.deepEqual(input.credential,credential);assert.equal(proof.call_digest,hash(input.call));assert.equal(proof.credential_digest,hash(credential));assert.ok(!nonces.has(proof.nonce));nonces.add(proof.nonce);assert.ok(verify(null,transcript('secs.holder-session-presentation.v1/signature',proof),child(),Buffer.from(input.proof.signature,'hex')));reads++;
    const path=input.call.parameters.path.split('?')[0];return send(fixture.reads[path]??{items:[]});
   }
   return send({},'application/json',404);
  });
  await page.goto(origin+'/monitor#topology');await page.waitForURL('**/login?**');
  await page.getByRole('button',{name:'Sign in with Wallet',exact:true}).waitFor({state:'visible'});
  await page.screenshot({path:join(output,`login-${tailnet?'tailnet':'public'}-${width}.png`),fullPage:true});
  const popupWait=context.waitForEvent('page');await page.getByRole('button',{name:'Sign in with Wallet',exact:true}).click();const popup=await popupWait;
  const terminal=terminalState!==null;
  const firstRead=terminal?null:page.waitForResponse(response=>new URL(response.url()).pathname==='/monitor/wallet-access/v1/requests'&&response.status()===200);
  await page.getByRole('button',{name:'Continue in Wallet…',exact:true}).waitFor();await popup.getByRole('button',{name:'Approve temporary session'}).click();
  if(terminal){
   if(manual){
    await page.getByRole('button',{name:'Check sign-in status',exact:true}).waitFor();
    await page.getByText('The original sign-in result is uncertain. Check its status before starting another session.',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Check sign-in status',exact:true}).click();
   }
   await page.getByRole('button',{name:'Sign in with Wallet',exact:true}).waitFor();
   await page.getByText(terminalState==='revoked'?'This session is no longer authorized. Sign in to check your current access.':'Your temporary session expired. Sign in to review a new one.',{exact:true}).waitFor();
   assert.equal(await page.getByText('The original sign-in result is uncertain. Check its status before starting another session.',{exact:true}).count(),0);
   assert.deepEqual(calls,['open','complete']);assert.equal(popups,1);assert.equal(statuses,manual?2:1);assert.equal(reads,0);assert.deepEqual(errors,[]);
   const saved=await page.evaluate(async()=>{const {BrowserSessionStore}=await import('/assets/credential-v2/session-store.mjs');return new BrowserSessionStore().load('devgraph.session.v1:'+location.origin+':service:fixture');});
   assert.equal(saved,null);results.push({origin,width,scenario,popups,reads,statuses,terminal_message:true,no_automatic_login:true});await context.close();continue;
  }
  await page.waitForURL(origin+'/monitor#topology');
  if(scenario==='transient-status'){await page.getByText('Connection paused · your existing sign-in is preserved',{exact:true}).waitFor();assert.equal(popups,1);}
  await page.getByText('Signed in with Wallet',{exact:true}).waitFor();
  // Authentication is rendered before graph loading completes. Wait for a real
  // authenticated read, rather than racing its asynchronous fetch in CI.
  await firstRead;
  const expiry=await page.locator('[data-session-expiry]').textContent();assert.equal(popups,1);assert.deepEqual(calls,['open','complete']);assert.ok(reads>0);
  assert.equal(await page.locator('.wallet-connection-steps').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const stored=await page.evaluate(async()=>{const {BrowserSessionStore}=await import('/assets/credential-v2/session-store.mjs');const row=await new BrowserSessionStore().load('devgraph.session.v1:'+location.origin+':service:fixture');let exported=false;try{await crypto.subtle.exportKey('jwk',row.value.privateKey);exported=true;}catch{}return{schema:row.value.schema,extractable:row.value.privateKey.extractable,exported,fields:Object.keys(row.value)};});
  assert.equal(stored.extractable,false);assert.equal(stored.exported,false);assert.equal(stored.fields.includes('parent_grant'),false);
  await page.evaluate(async()=>{
   const {BrowserSessionStore}=await import('/assets/credential-v2/session-store.mjs'),store=new BrowserSessionStore();
   await store.reserve('qa-cas','old');await store.remove('qa-cas','old');
   let rejected=false;try{await store.save('qa-cas','old',{credential:'synthetic'});}catch(e){rejected=e.code==='browser_session_changed';}
   if(!rejected)throw Error('logout_resurrection');
   await store.reserve('qa-cas','new');await store.remove('qa-cas','old');
   if((await store.load('qa-cas')).epoch!=='new')throw Error('old_logout_deleted_new_session');await store.remove('qa-cas','new');
  });
  await page.reload();await page.getByText('Signed in with Wallet',{exact:true}).waitFor();assert.equal(await page.locator('[data-session-expiry]').textContent(),expiry);assert.deepEqual(calls,['open','complete']);
  await page.goto(origin+'/monitor/kanban/');await page.getByText('Signed in with Wallet',{exact:true}).waitFor();assert.deepEqual(calls,['open','complete']);assert.ok(statuses>=3);
  const other=await context.newPage();other.on('pageerror',e=>errors.push(e.message));await other.goto(origin+'/monitor');await other.getByText('Signed in with Wallet',{exact:true}).waitFor();
  // Exercise the pagehide/pageshow hooks without claiming this synthetic event
  // is a complete browser BFCache implementation test.
  await other.evaluate(()=>{dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));});
  await other.getByText('Signed in with Wallet',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();await page.waitForURL(origin+'/login');await page.getByRole('button',{name:'Sign in with Wallet',exact:true}).waitFor();assert.equal(popups,1);
  await other.waitForURL('**/login?**');await other.close();
  assert.deepEqual(errors,[]);results.push({origin,width,scenario,popups,reads,statuses,expiry_preserved:true,nonextractable:true});await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'Real shells and browser custody; synthetic Wallet/issuer, no personal data or approvals',results},null,2));console.log('Popup login browser walkthrough passed (28 desktop/mobile public/Tailnet normal/lost-reply/transient and terminal-recovery scenarios).');
}finally{await browser.close();}
