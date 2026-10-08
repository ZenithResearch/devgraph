// Disposable extension test: actual Chrome events/provider/routing, synthetic custody/native.
// Every page request is fulfilled locally; this never contacts the installed preview server.
import {runtimeFiles} from '../scripts/artifact-files.mjs';
import assert from 'node:assert/strict';
import {mkdtemp, cp, readFile, writeFile, mkdir, rm} from 'node:fs/promises';
import {resolve, extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {tmpdir, platform, arch} from 'node:os';
import {chromium} from 'playwright';

if (!process.argv[2]) throw Error('Pass the exact built Castalia Wallet worktree as the first argument');
const wallet = resolve(process.argv[2]);
const root = resolve(fileURLToPath(new URL('../../../', import.meta.url)));
const sdkDist = resolve(root, 'packages/web/dist');
const walletApp = resolve(wallet, 'apps/chrome-extension');
const allowed = 'http://127.0.0.1:8080/sdk-preview/';
const origin = new URL(allowed).origin;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const git = (cwd, ...args) => execFileSync('git', args, {cwd, encoding:'utf8'}).trim();
const modules = ['devgraph-preview-runtime.js', 'devgraph-bridge.js', 'devgraph-native-control.js',
  'devgraph-operation-broker.js', 'devgraph-work-consent.js', 'content-script.js', 'provider.js', 'devgraph-preview-confirmation.js'];
const walletHashes = Object.fromEntries(await Promise.all(modules.map(async name => [name, hash(await readFile(resolve(walletApp, 'dist', name)))])));
const scratch = await mkdtemp(resolve(tmpdir(), 'devgraph-wallet-browser-'));
const extension = resolve(scratch, 'extension');
let context;
const cases = [];
try {
  await mkdir(resolve(extension, 'src'), {recursive:true});
  await cp(resolve(walletApp, 'dist'), resolve(extension, 'dist'), {recursive:true});
  for (const name of ['devgraph-preview-confirmation.html', 'confirmation.css'])
    await cp(resolve(walletApp, 'src', name), resolve(extension, 'src', name));
  await writeFile(resolve(extension, 'manifest.json'), JSON.stringify({
    manifest_version:3, name:'Devgraph disposable lifecycle fixture', version:'0.0.1',
    permissions:['storage', 'tabs', 'webNavigation'],
    host_permissions:[`${origin}/*`],
    background:{service_worker:'fixture-worker.js', type:'module'},
    content_scripts:[{matches:[`${origin}/*`], js:['dist/content-script.js'], run_at:'document_start'}],
    web_accessible_resources:[{resources:['dist/provider.js'], matches:[`${origin}/*`]}],
  }));
  await writeFile(resolve(extension, 'fixture-worker.js'), `
import {installDevgraphPreviewRuntime} from './dist/devgraph-preview-runtime.js';
const event=()=>{const listeners=new Set();return {addListener:f=>listeners.add(f),removeListener:f=>listeners.delete(f),fire:(...a)=>{for(const f of [...listeners])f(...a)}}};
globalThis.fixture={opened:0,closed:0,actions:[],events:[]};
for(const name of ['onCommitted','onHistoryStateUpdated','onReferenceFragmentUpdated'])
  chrome.webNavigation[name].addListener(details=>fixture.events.push({name,...details}));
const actor='pubkey:sha256:'+'22'.repeat(32);
const bytes=new TextEncoder().encode(JSON.stringify({id:'fixture',kind:'Issue',title:'Browser fixture',description:'',status:'draft',version:1,priority:0,artifact_ids:[],external_link_ids:[]}));
const encoded=btoa(String.fromCharCode(...bytes)).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fakeNative=()=>{
  const serial=++fixture.opened,onMessage=event(),onDisconnect=event();let closed=false,stream=0;
  return {onMessage,onDisconnect,disconnect(){if(!closed){closed=true;fixture.closed++;onDisconnect.fire()}},postMessage(m){
    fixture.actions.push({action:m.action,connection:serial});
    let result;
    if(m.action==='connect')result={connection_id:'connection-'+serial,actor_id:actor,receiver_profile:'302680d9f3a263a4897bc855abcebc6fa9abbca00d08c69af76757da88b07e5a',stable_issuer:'secs:test-work',audience:'devgraph://receiver-local',origin:'${origin}',capabilities:['read','work.v1']};
    else if(m.action==='request_read_access')result={read_context:'read-'+serial,expires_at:Math.floor(Date.now()/1000)+900};
    else if(m.action==='read')result={stream_id:'stream-'+serial+'-'+(++stream),status:200,content_type:'application/json',content_encoding:'identity',content_length:String(bytes.length),limit:8388608,dispatched:false};
    else if(m.action==='pull')result={stream_id:m.stream_id,seq:0,total:bytes.length,chunk_b64:encoded,done:true};
    else if(m.action==='cancel')result={cancelled:true,dispatched:false};
    else throw Error('Unexpected native fixture action '+m.action);
    queueMicrotask(()=>{if(!closed)onMessage.fire({v:1,id:m.id,ok:true,result})});
  }};
};
installDevgraphPreviewRuntime({runtime:{getURL:path=>chrome.runtime.getURL(path),onConnect:chrome.runtime.onConnect,onMessage:chrome.runtime.onMessage,connectNative:fakeNative},windows:chrome.windows,webNavigation:chrome.webNavigation,storage:chrome.storage},{
  withCustody:async f=>f(),getState:async()=>({setupComplete:true,unlocked:true,publicKey:'11'.repeat(32)}),approvedOrigins:async()=>['${origin}'],actorId:()=>actor,epoch:()=>1,onCustodyChanged:()=>()=>{},prepare:()=>{throw Error('Read-only fixture')}
});
`);
  context = await chromium.launchPersistentContext(resolve(scratch, 'profile'), {
    channel:'chromium', headless:true, args:[`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  context.setDefaultTimeout(15_000);
  await context.route(/^https?:\/\//, async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname.startsWith('/sdk/')) {
      const name = resolve(sdkDist, url.pathname.slice(5));
      if (!name.startsWith(`${sdkDist}/`)) return route.abort();
      try { return route.fulfill({body:await readFile(name), contentType:extname(name)==='.wasm'?'application/wasm':'text/javascript'}); }
      catch { return route.fulfill({status:404, body:''}); }
    }
    if (url.pathname === '/fixture.js') return route.fulfill({contentType:'text/javascript', body:`
import {initialize,connectCastalia} from '/sdk/index.js';
window.fixture={};
const run=async(action)=>{fixture.result=null;try{fixture.result={ok:true,value:await action()}}catch(error){fixture.result={ok:false,code:error.code,message:String(error)}}};
document.querySelector('#connect').onclick=()=>run(async()=>{fixture.runtime??=await initialize();fixture.connection=await connectCastalia();fixture.client=fixture.runtime.createClient({connection:fixture.connection});return fixture.connection.profile.connection_id});
document.querySelector('#consent').onclick=()=>run(async()=>{fixture.grant=await fixture.connection.requestReadAccess();return true});
document.querySelector('#read').onclick=()=>run(async()=>{const work=await fixture.client.getWork('Issue','fixture',{read_context:fixture.grant});return work.title});
`});
    return route.fulfill({contentType:'text/html', headers:{'content-security-policy':"default-src 'self'; script-src 'self' 'wasm-unsafe-eval' chrome-extension:; style-src 'self'; frame-src 'self'"}, body:'<!doctype html><title>Disposable Devgraph fixture</title><button id="connect">Connect</button><button id="consent">Read access</button><button id="read">Read</button><script type="module" src="/fixture.js"></script>'});
  });
  const worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
  const stats = () => worker.evaluate(() => structuredClone(globalThis.fixture));
  const page = await context.newPage();
  await page.goto(allowed);
  await page.waitForFunction(() => typeof window.castaliaWallet?.devgraph === 'function' && window.fixture);
  const action = async selector => {
    await page.click(selector);
    await page.waitForFunction(() => window.fixture.result !== null);
    return page.evaluate(() => window.fixture.result);
  };
  const connect = async () => {const result=await action('#connect');assert.equal(result.ok,true,JSON.stringify(result));};
  const consent = async () => {
    const popupReady = context.waitForEvent('page');
    await page.click('#consent');
    const popup = await popupReady;
    await popup.locator('#approve').click();
    await page.waitForFunction(() => window.fixture.result !== null);
    assert.equal((await page.evaluate(() => window.fixture.result)).ok,true);
    await page.bringToFront();
  };
  const read = async expected => assert.equal((await action('#read')).ok,expected);
  // Poll with a deadline, without requiring test-only browser APIs in production code.
  const until = async predicate => {
    const deadline=Date.now()+15_000;
    while(!await predicate()) {if(Date.now()>deadline)throw Error('Browser lifecycle condition timed out: '+JSON.stringify(await stats()));await new Promise(r=>setTimeout(r,25));}
  };
  await connect(); await consent(); await read(true); await read(true);
  cases.push('actual provider injection, trusted confirmation popup, two complete reads');
  const baseline = await stats();
  await page.evaluate(() => history.replaceState({same:true},'',location.href));
  await until(async()=> (await stats()).events.some(e=>e.name==='onHistoryStateUpdated'));
  await read(true);
  assert.equal((await stats()).closed,baseline.closed);
  cases.push('same URL history state retains authority');
  await page.evaluate(() => {const frame=document.createElement('iframe');frame.src='/subframe/';document.body.append(frame)});
  await until(async()=> (await stats()).events.some(e=>e.frameId!==0));
  await read(true);
  cases.push('unrelated subframe navigation retains top-frame authority');
  for (const mode of ['pushState','replaceState','fragment','back-forward']) {
    const before=await stats();
    if(mode==='fragment') await page.evaluate(()=>{location.hash='departed'});
    else await page.evaluate(mode=>history[mode==='replaceState'?'replaceState':'pushState']({},'','/sdk-preview/?departed'),mode);
    await until(async()=> (await stats()).closed===before.closed+1);
    // No SDK call is made between departure and return: Chrome events must revoke.
    if(mode==='back-forward') {
      await page.evaluate(()=>history.back()); await page.waitForURL(allowed);
      await page.evaluate(()=>history.forward()); await page.waitForURL(/departed/);
    }
    await page.evaluate(url=>history.replaceState({},'',url),allowed);
    await read(false);
    assert.equal((await stats()).closed,before.closed+1,'revocation is idempotent');
    await connect(); await consent(); await read(true);
    cases.push(`${mode}: departure/return cannot revive old grant; fresh connection and consent succeed`);
  }
  const beforeDispose=await stats();
  const disposed=await page.evaluate(()=>window.castaliaWallet.devgraph({v:1,request_id:crypto.randomUUID(),action:'dispose',connection_id:window.fixture.connection.profile.connection_id}));
  assert.deepEqual(disposed,{disposed:true},'accepted provider disposal acknowledges before the port closes');
  await until(async()=> (await stats()).closed===beforeDispose.closed+1);
  await read(false);
  await connect(); await consent(); await read(true);
  cases.push('direct provider dispose acknowledges success, releases authority and permits a fresh connection');
  const beforePrompt=await stats();
  const promptReady=context.waitForEvent('page');
  await page.click('#consent');
  const prompt=await promptReady;
  await prompt.locator('#approve').waitFor({state:'visible'});
  await page.evaluate(()=>history.pushState({},'','/sdk-preview/?during-consent'));
  await until(async()=> (await stats()).closed===beforePrompt.closed+1);
  await until(async()=> prompt.isClosed());
  await page.waitForFunction(()=>window.fixture.result!==null);
  assert.equal((await page.evaluate(()=>window.fixture.result)).ok,false);
  cases.push('navigation cancels a real pending confirmation window and settles the SDK promise');
  const result=await stats();
  assert.equal(result.opened,result.closed,'all native fixture ports released');
  for(const name of ['onCommitted','onHistoryStateUpdated','onReferenceFragmentUpdated'])
    assert.ok(result.events.some(e=>e.name===name),`actual ${name} observed`);
  const evidence={scope:'disposable Chrome extension; actual provider/content/runtime/bridge/NativeControl and navigation; synthetic custody/native endpoints; no installed host or receiver',
    node:process.version,platform:platform(),arch:arch(),browser_version:context.browser().version(),
    sdk_commit:git(root,'rev-parse','HEAD'),sdk_dirty:Boolean(git(root,'status','--porcelain')),
    wallet_commit:git(wallet,'rev-parse','HEAD'),wallet_dirty:Boolean(git(wallet,'status','--porcelain')),
    fixture_extension_id:new URL(worker.url()).host,wallet_module_sha256:walletHashes,
    wasm_sha256:hash(await readFile(resolve(sdkDist,'internal/devgraph_web_bg.wasm'))),
    runtime_js_sha256:hash(Buffer.concat(await Promise.all(runtimeFiles.map(file=>readFile(resolve(sdkDist,file)))))),
    test_sha256:hash(await readFile(fileURLToPath(import.meta.url))),cases,native_ports:{opened:result.opened,closed:result.closed},events:result.events};
  await mkdir(resolve(root,'.sdk-validation'),{recursive:true});
  await writeFile(resolve(root,'.sdk-validation/wallet-browser.json'),JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify({cases,ports:evidence.native_ports,browser:evidence.browser_version}));
} finally {
  await context?.close();
  await rm(scratch,{recursive:true,force:true});
}
