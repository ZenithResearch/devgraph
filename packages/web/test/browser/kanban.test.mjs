import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,firefox,webkit} from 'playwright';
const root=resolve(fileURLToPath(new URL('../../../../',import.meta.url)));
const [vector]=JSON.parse(await readFile(resolve(root,'crates/devgraph-client-core/tests/fixtures/progress-results.json')));
for(const [name,engine] of Object.entries({chromium,firefox,webkit}))test(`${name}: served Kanban loads the shared decoder and rejects a partial receipt`,async()=>{
 let origin, mode='valid';const requests=[];
 const server=createServer(async(req,res)=>{
  const path=new URL(req.url,'http://localhost').pathname;
  if(path==='/'){res.setHeader('content-type','text/html');res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'");res.end('<!doctype html><title>Kanban decoder qualification</title>');return;}
  if(path.startsWith('/credential-work/v2/')){
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks));requests.push({path,body});
   let result;
   if(path.endsWith('/capabilities'))result={schema:'devgraph.credential-transport-capabilities.v2',operations:['devgraph.work.create.v2','devgraph.work.workflow.transition.v2']};
   else if(path.endsWith('/provider-profile'))result={schema:'castalia.provider-profile.v1',display_name:'Devgraph',origins:[origin],membership:null,presentations:[{issuer:'secs:test',key_id:'test',public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:origin}]}]};
   else if(path.endsWith('/prepare'))result={schema:'castalia.credential-presentation-request.v2',credential:{},disclosure:{}};
   else if(path.endsWith('/status'))result={state:'committed',receipt:{...vector.result.receipt,duplicate:true}};
   else result=mode==='valid'?{state:'committed',...vector.result}:{state:'committed',receipt:vector.result.receipt};
   res.setHeader('content-type','application/json');res.end(JSON.stringify(result));return;
  }
  const mounts=[['/assets/devgraph-sdk/',resolve(root,'packages/web/dist')],['/assets/',resolve(root,'src/devgraph/frontend/static')]];
  for(const [prefix,base] of mounts)if(path.startsWith(prefix)){
   const file=resolve(base,path.slice(prefix.length));if(!file.startsWith(base+'/'))break;
   try{res.setHeader('content-type',extname(file)==='.wasm'?'application/wasm':'text/javascript');res.end(await readFile(file));return;}catch{break;}
  }
  res.writeHead(404);res.end();
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;let browser;
 try{
  browser=await engine.launch({headless:true});const page=await browser.newPage();await page.goto(origin);
  await page.evaluate(async()=>{
   globalThis.castaliaWallet={getCapabilities:async()=>['credential_presentation_v2','provider_profiles_v1','connection_v1'],getSubject:async()=>({publicKey:'ab'.repeat(32)}),proposeProviderProfile:async()=>({state:'approved'}),requestConnection:async()=>({state:'connected'}),presentCredential:async()=>({state:'approved',presentation:{schema:'castalia.credential-presentation.v2'}})};
   const {Wallet}=await import('/assets/kanban/wallet.mjs');globalThis.wallet=new Wallet();await wallet.prepareSetup();await wallet.connect();
  });
  const raw=JSON.stringify(vector.request),key='browser-kanban-request-001';
  assert.equal(await page.evaluate(async({raw,key})=>(await wallet.execute(raw,key)).state,{raw,key}),'committed');
  mode='partial';
  assert.equal(await page.evaluate(async({raw,key})=>{try{await wallet.execute(raw,key+'-partial');return false;}catch(e){return e.dispatched;}},{raw,key}),true);
  assert.equal(await page.evaluate(async({raw,key})=>(await wallet.reconcile(raw,key+'-partial')).receipt.duplicate,{raw,key}),true);
  assert.equal(requests.filter(r=>r.path.endsWith('/execute')).length,2);
  const original=requests.filter(r=>r.path.endsWith('/execute')).at(-1).body;
  const recovered=requests.find(r=>r.path.endsWith('/status')).body;
  assert.equal(original.request,recovered.request);assert.equal(original.idempotency_key,recovered.idempotency_key);
 }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
});
