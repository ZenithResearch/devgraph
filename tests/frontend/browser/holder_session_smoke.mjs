/** Real browser crypto/UI; synthetic Wallet approval and issuer, no user data. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createPrivateKey,createPublicKey,createHash,sign,verify,randomBytes} from 'node:crypto';
const require=createRequire(process.env.DEVGRAPH_PLAYWRIGHT_PACKAGE??new URL('../../../packages/web/package.json',import.meta.url));
const {chromium}=require('playwright-core');
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const output=process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-holder-session-ui';await mkdir(output,{recursive:true});
const markup=(await readFile(join(repo,'src/devgraph/frontend/wallet.py'),'utf8')).match(/r'''([\s\S]*?)'''/)[1];
const key=createPrivateKey({key:Buffer.from('302e020100300506032b657004220420'+'3d'.repeat(32),'hex'),format:'der',type:'pkcs8'});
const publicKey=createPublicKey(key).export({format:'der',type:'spki'}).subarray(-32).toString('hex');
const canonical=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
const transcript=(domain,v)=>Buffer.from(domain+'\0'+canonical(v));
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
const browser=await chromium.launch({headless:true,executablePath:process.env.DEVGRAPH_CHROMIUM_EXECUTABLE});
const results=[];
try{
 for(const origin of ['https://graph.example','https://graph-example.tail123abc.ts.net:9443'])for(const width of [1280,375])for(const canonicalRoute of [false,true]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
  page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
  let challenge,credential,preparations=0,issued=0,reads=0;const nonces=new Set();
  const pin={issuer:'synthetic',key_id:'key',public_key:publicKey,service:'service:synthetic'};
  const profile={schema:'castalia.provider-profile.v1',display_name:'Synthetic issuer',origins:[origin],membership:null,presentations:[{issuer:pin.issuer,key_id:pin.key_id,public_key:pin.public_key,audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};
  const prefix=canonicalRoute?'/service/v1':'',now=Math.floor(Date.now()/1000);
  const routeClaims={schema:'secs.service-access-point.v1',issuer:pin.issuer,key_id:pin.key_id,service:pin.service,origin,authority_receiver:origin,prefix:'/service/v1',not_before:now-30,not_after:now+3600};
  const binding={claims:routeClaims,signature:sign(null,transcript('secs.service-access-point.v1',routeClaims),key).toString('hex')};
  await page.addInitScript(canonicalRoute=>{
   window.fixtureWalletCalls=[];window.fixtureLocked=false;
   const post=async(path,body)=>(await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
   window.devgraphWalletProvider={
    getCapabilities:async()=>['provider_profiles_v1','connection_v1','read_access_v1','read_setup_status_v1','holder_grant_v1','holder_grant_v2','holder_session_exchange_v1',...(canonicalRoute?['service_access_point_v1']:[])],
    proposeProviderProfile:async()=>{throw Error('unexpected profile approval')},requestConnection:async()=>{throw Error('unexpected site approval')},
    readAccess:async input=>{
     window.fixtureWalletCalls.push(input.operation);if(window.fixtureLocked)throw Error('wallet_locked');
     if(input.operation==='setup_status')return{schema:'castalia.read-setup-status.v1',connection_approved:true,profile_approved:true};
     if(input.operation==='identity')return{subject_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),memberships:[]};
     if(input.operation==='prepare_holder_session')return post('/fixture/challenge',input);
     if(input.operation==='open_holder_session')return new Promise((resolve,reject)=>{window.fixtureApprove=()=>post('/fixture/approve',input).then(resolve,reject)});
     throw Error('unexpected Wallet operation '+input.operation);
    }
   };
  },canonicalRoute);
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin);
   const send=(value,type='application/json')=>route.fulfill({status:200,contentType:type,body:typeof value==='string'?value:JSON.stringify(value)});
   if(url.pathname==='/')return send('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;margin:16px;--aqua:#185c48;--canvas:white;--muted:#284c40}button,a{padding:12px}</style><link rel="stylesheet" href="/monitor/topology-assets/wallet-access.css">'+markup+'<button id="fixture-approve">Approve synthetic Wallet session</button><button id="fixture-read">Read shared work</button><a href="#topology" id="topology">Topology</a><p id="read-result"></p><script src="/monitor/topology-assets/wallet-access.js"></script><script>window.fixtureController=DevgraphWalletAccess.mount(document.querySelector("#wallet-read-connection"));document.querySelector("#fixture-approve").onclick=()=>window.fixtureApprove?.();document.querySelector("#fixture-read").onclick=()=>fixtureController.read("/todos/v2?limit=1").then(v=>document.querySelector("#read-result").textContent="Verified shared work "+v.total);</script>','text/html');
   const folder=url.pathname.startsWith('/monitor/topology-assets/')?'topology':url.pathname.startsWith('/assets/credential-v2/')?'credential-v2':null;
   if(folder)return send(await readFile(join(repo,'src/devgraph/frontend/static',folder,url.pathname.split('/').at(-1)),'utf8'),url.pathname.endsWith('.css')?'text/css':'text/javascript');
   if(url.pathname==='/monitor/wallet-access/v1')return send({schema:'devgraph.wallet-read-configuration.v1',endpoint:url.pathname,provider_profile:profile,...(canonicalRoute?{service_access_point_endpoint:'/.well-known/devgraph-service.json'}:{}),holder_grants:{schema:canonicalRoute?'secs.holder-grant.v1':'secs.holder-grant.v2',service:pin.service,endpoint:url.pathname+'/grants',...(canonicalRoute?{v2_endpoint:url.pathname+'/grants/v2',supported_schemas:['secs.holder-grant.v1','secs.holder-grant.v2']}:{})}});
   if(canonicalRoute&&url.pathname==='/.well-known/devgraph-service.json')return send({schema:'devgraph.service-access-point.v1',binding});
   if(url.pathname===prefix+'/holder-sessions/v1/capabilities')return send({schema:'secs.holder-session-capabilities.v1',supported:true,profile:'secs.holder-session.v1',issuer_pin:pin,audience:'devgraph',grant_schemas:['secs.holder-grant.v2'],credential_schema:'secs.holder-session-credential.v1',presentation_schema:'secs.holder-session-presentation.v1',read_only:true,max_ttl_seconds:600,review_ttl_seconds:120,proof_max_ttl_seconds:60,challenge_path:prefix+'/holder-sessions/v1/challenges',exchange_path:prefix+'/holder-sessions/v1/exchange',status_path:prefix+'/holder-sessions/v1/status'});
   if(url.pathname==='/fixture/challenge'){
    preparations++;const input=request.postDataJSON(),now=Math.floor(Date.now()/1000);
    if(canonicalRoute)assert.deepEqual(input.service_access_point,binding);
    challenge={schema:'secs.holder-session-challenge.v1',challenge_id:randomBytes(16).toString('hex'),request_id:randomBytes(16).toString('hex'),nonce:randomBytes(32).toString('hex'),issuer:pin.issuer,key_id:pin.key_id,service:pin.service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'2'.repeat(64),holder_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),session_public_key:input.session_public_key,session_installation_id:input.session_installation_id,permissions:[{operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph',constraints:{},roles:[]}],issued_at:now,review_deadline:now+60,expires_at:now+60};
    return send({schema:'castalia.holder-session-prepared.v1',preparationId:'synthetic-preparation',challenge});
   }
   if(url.pathname==='/fixture/approve'){
    const input=request.postDataJSON();assert.deepEqual(input.challenge,challenge);
    const clientKey=createPublicKey({key:Buffer.from('302a300506032b6570032100'+challenge.session_public_key,'hex'),format:'der',type:'spki'});
    assert.ok(verify(null,transcript('secs.holder-session-possession.v1/signature',challenge),clientKey,Buffer.from(input.session_signature,'hex')));
    const claims={schema:'secs.holder-session-credential.v1',credential_id:randomBytes(16).toString('hex'),challenge_digest:hash(challenge),challenge};
    credential={claims,signature:sign(null,transcript('secs.holder-session-credential.v1/signature',claims),key).toString('hex')};issued++;
    return send({schema:'castalia.holder-session-opened.v1',credential});
   }
   if(url.pathname===prefix+'/monitor/wallet-access/v1/requests'){
    const envelope=request.postDataJSON(),proof={...envelope.proof};delete proof.signature;
    assert.deepEqual(envelope.credential,credential);assert.equal(proof.call_digest,hash(envelope.call));assert.equal(proof.credential_digest,hash(credential));assert.ok(proof.expires_at<=challenge.expires_at);assert.ok(!nonces.has(proof.nonce));nonces.add(proof.nonce);
    const clientKey=createPublicKey({key:Buffer.from('302a300506032b6570032100'+challenge.session_public_key,'hex'),format:'der',type:'spki'});
    assert.ok(verify(null,transcript('secs.holder-session-presentation.v1/signature',proof),clientKey,Buffer.from(envelope.proof.signature,'hex')));reads++;return send({items:[],total:reads});
   }
   throw Error('unexpected synthetic route '+url.pathname);
  });
  await page.goto(origin);await page.getByRole('button',{name:'Start read session',exact:true}).waitFor();
  assert.equal(issued,0);assert.equal(preparations,1);
  await page.getByRole('button',{name:'Start read session',exact:true}).click();
  await page.getByText('Opening Wallet approval. A read connection is not active yet.',{exact:true}).waitFor();assert.equal(issued,0);
  await page.locator('#fixture-approve').click();await page.getByText('Browser read session · Read only',{exact:true}).waitFor();
  assert.match(await page.locator('[data-wallet-session-expiry]').textContent(),/Using it does not extend this time\./);
  await page.locator('#fixture-read').click();await page.getByText('Verified shared work 1',{exact:true}).waitFor();
  await page.evaluate(()=>{window.fixtureLocked=true;window.dispatchEvent(new Event('castalia:wallet:read-access-invalidated'));});
  await page.locator('#topology').click();await page.locator('#fixture-read').click();await page.getByText('Verified shared work 2',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.fixtureController.connected),true);assert.equal(issued,1);assert.equal(preparations,1);
  assert.equal((await page.evaluate(()=>window.fixtureWalletCalls)).filter(x=>x==='open_holder_session').length,1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:join(output,`session-${origin.includes('tail123abc')?'tailnet':'public'}-${width}-${canonicalRoute?'canonical':'legacy'}.png`),fullPage:true});
  await page.clock.install({time:new Date()});await page.clock.fastForward(61000);
  await page.getByText('Your read access expired. Connect Wallet to review fresh access.',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.fixtureController.connected),false);assert.equal(issued,1);assert.equal(preparations,1);assert.deepEqual(errors,[]);
  results.push({origin,width,canonical_route:canonicalRoute,approvals:1,preparations,issued,reads,lock_independent:true,hash_preserves_session:true,fixed_idle_expiry:true});await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({qualification:'real browser temporary keys and application UI with synthetic Wallet approval/issuer only; not installed Wallet or native authorization evidence',results},null,2)+'\n');
 console.log('Finite browser session walkthrough passed (8 scenarios).');
}finally{await browser.close();}
