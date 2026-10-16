// Run after explicitly packing/installing the tarball in the selected external directory.
// The consumer uses only its installed SDK, TypeScript and Vite; no Rust/Git/build hooks.
import {runtimeFiles} from '../scripts/artifact-files.mjs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir, readdir} from 'node:fs/promises';
import {dirname, resolve, extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, firefox, webkit} from 'playwright';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../..');
const consumer=resolve(process.argv[2]??'/private/tmp/devgraph-sdk-consumer');
assert.ok(!consumer.startsWith(root+'/'),'consumer must be outside the repository');
const installed=resolve(consumer,'node_modules/@devgraph/web');
const packageJson=JSON.parse(await readFile(resolve(installed,'package.json')));
const [todoFixture]=JSON.parse(await readFile(resolve(root,'crates/devgraph-client-core/tests/fixtures/progress-results.json')));
assert.equal(packageJson.name,'@devgraph/web');
for(const hook of ['preinstall','install','postinstall','prepare'])assert.equal(packageJson.scripts?.[hook],undefined);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const tarballName=`devgraph-web-${packageJson.version}.tgz`;
const tarball=await readFile(resolve(consumer,tarballName));
assert.equal(hash(tarball),hash(await readFile(resolve(root,'packages/web',tarballName))),
  'consumer evidence must name the current packed artifact');
const lock=JSON.parse(await readFile(resolve(consumer,'package-lock.json')));
assert.equal(lock.packages['node_modules/@devgraph/web'].integrity,
  'sha512-'+createHash('sha512').update(tarball).digest('base64'),
  'npm must install the exact evidence artifact');
const installedManifest=await readFile(resolve(installed,'sdk-build.json'),'utf8');
assert.equal(installedManifest,await readFile(resolve(root,'packages/web/sdk-build.json'),'utf8'),
  'installed provenance must match the public candidate');
const sourceManifest=JSON.parse(installedManifest);
assert.equal(sourceManifest.source_dirty,false,'qualification requires committed source');

const installedWasm=await readFile(resolve(installed,'dist/internal/devgraph_web_bg.wasm'));
const installedJs=await readFile(resolve(installed,'dist/index.js'));
const installedFactory=await readFile(resolve(installed,'dist/internal/devgraph_web_factory.js'));
assert.equal(hash(installedWasm),hash(await readFile(resolve(root,'packages/web/dist/internal/devgraph_web_bg.wasm'))));
assert.equal(hash(installedJs),hash(await readFile(resolve(root,'packages/web/dist/index.js'))));
assert.equal(hash(installedFactory),hash(await readFile(resolve(root,'packages/web/dist/internal/devgraph_web_factory.js'))));
for(const file of runtimeFiles)assert.equal(hash(await readFile(resolve(installed,'dist',file))),hash(await readFile(resolve(root,'packages/web/dist',file))));
const run=(script,args=[])=>execFileSync(process.execPath,[script,...args],{cwd:consumer,encoding:'utf8',stdio:['ignore','pipe','pipe']});

await writeFile(resolve(consumer,'ssr.mjs'),`
import assert from 'node:assert/strict';
for(const name of ['window','document','castaliaWallet'])Object.defineProperty(globalThis,name,{get(){throw Error('browser side effect')}});
globalThis.fetch=()=>{throw Error('import network side effect')};
const sdk=await import('@devgraph/web');
assert.equal(typeof sdk.initialize,'function');
assert.equal(sdk.exportJson({version:9223372036854775807n}),'{'+'"version":"9223372036854775807"'+'}');
console.log('external SSR import passed');
`);
await writeFile(resolve(consumer,'plain-esm.mjs'),`
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initialize,connectCastalia} from '@devgraph/web';
const bytes=new Uint8Array(await readFile(new URL(import.meta.resolve('@devgraph/web/wasm'))));
const module=await WebAssembly.compile(bytes);
const runtime=await initialize({wasm:bytes});runtime.dispose();
const compiled=await initialize({wasm:module});compiled.dispose();
await assert.rejects(initialize({wasm:new Uint8Array([1,2,3])}),{code:'initialization_failed'});
(await initialize({wasm:bytes})).dispose();
await assert.rejects(connectCastalia(),{code:'capability_unavailable'});
console.log('external plain ESM and custom WASM initialization passed');
`);
await writeFile(resolve(consumer,'tsconfig.json'),JSON.stringify({compilerOptions:{target:'ES2022',module:'ESNext',moduleResolution:'Bundler',strict:true,exactOptionalPropertyTypes:true,noUncheckedIndexedAccess:true,noEmit:true,lib:['ES2022','DOM'],types:['vite/client']},include:['types.ts','main.ts']},null,2));
await writeFile(resolve(consumer,'types.ts'),`
import type {Client,PreparedOperation,CastaliaConnection,Work,WorkRequestInput,MutationOutcome,CypherResult} from '@devgraph/web';
type IsNotAny<T> = 0 extends (1 & T) ? false : true;
const workResultIsTyped: IsNotAny<Awaited<ReturnType<Client['getWork']>>> = true;
const canonicalBytesAreTyped: IsNotAny<PreparedOperation['canonical_bytes']> = true;
const profileIsTyped: IsNotAny<CastaliaConnection['profile']> = true;
const cypherResultIsTyped: IsNotAny<Awaited<ReturnType<Client['cypher']>>> = true;
const valid: WorkRequestInput = {schema:'devgraph.work-request.v1',operation:'create',kind:'Issue',id:'example',expected_version:null,payload:{id:'example',title:'Example',priority:1n}};
const patch: WorkRequestInput = {schema:'devgraph.work-request.v1',operation:'patch',kind:'Issue',id:'example',expected_version:1n,payload:{title:'Changed'}};
// @ts-expect-error signed input cannot receive a string version
const stringVersion: WorkRequestInput = {...patch,expected_version:'1'};
// @ts-expect-error patch null is not omission
const nullPatch: WorkRequestInput = {...patch,payload:{title:null}};
// @ts-expect-error create needs its explicit null precondition
const missingVersion: WorkRequestInput = {schema:'devgraph.work-request.v1',operation:'create',kind:'Issue',id:'example',payload:{id:'example',title:'Example'}};
// @ts-expect-error unsupported Work kind
const badKind: WorkRequestInput = {...valid,kind:'Repository'};
// @ts-expect-error only Proposal can accept
const wrongAccept: WorkRequestInput = {schema:'devgraph.work-request.v1',operation:'accept',kind:'Issue',id:'example',expected_version:1,payload:{decision_id:'decision',decision_title:'Decision'}};
// @ts-expect-error archive has an empty payload
const nonemptyArchive: WorkRequestInput = {schema:'devgraph.work-request.v1',operation:'archive',kind:'Issue',id:'example',expected_version:1,payload:{title:'Forbidden'}};
function usesResults(work:Work,result:MutationOutcome,cypher:CypherResult,prepared:PreparedOperation){
 const version:bigint=work.version;
 const bytes:Uint8Array=prepared.canonical_bytes;
 const rows:ReadonlyArray<ReadonlyArray<string|bigint|boolean>>=cypher.rows;
 const count:bigint=cypher.row_count;
 // @ts-expect-error domain integers do not silently narrow to JS numbers
 const rounded:number=work.version;
 if(result.kind==='committed'){
   // @ts-expect-error duplicate Work is explicitly nullable
   const historical:Work=result.work;
   if(result.work!==null){const current:string|bigint=result.work.version;void current;}
 }
 void [version,bytes,rows,count,rounded];
}
void [workResultIsTyped,canonicalBytesAreTyped,profileIsTyped,cypherResultIsTyped,valid,stringVersion,nullPatch,missingVersion,badKind,wrongAccept,nonemptyArchive,usesResults];
`);
await writeFile(resolve(consumer,'index.html'),'<!doctype html><meta charset="utf-8"><title>Installed Devgraph SDK qualification</title><pre id="result">Starting</pre><script type="module" src="/main.ts"></script>');
await writeFile(resolve(consumer,'main.ts'),`
import {initialize,connectCastalia,exportJson} from '@devgraph/web';
import wasmUrl from '@devgraph/web/wasm?url';
const result=document.querySelector('#result')!;
const body='{"id":"example","kind":"Issue","title":"Fixture","description":"","status":"draft","version":9223372036854775807,"priority":-9223372036854775808,"artifact_ids":[],"external_link_ids":[]}';
const bytes=new TextEncoder().encode(body);
// A fixture transport tests the installed package; this does not qualify native authority.
(globalThis as typeof globalThis & {castaliaWallet:unknown}).castaliaWallet={devgraph:async(message:{action:string,request?:{kind:string}})=>{
 switch(message.action){
 case 'connect':return {connection_id:'consumer-connection',actor_id:'pubkey:sha256:'+'a'.repeat(64),receiver_profile:'302680d9f3a263a4897bc855abcebc6fa9abbca00d08c69af76757da88b07e5a',stable_issuer:'secs:test-work',audience:'devgraph://receiver-local',origin:'http://127.0.0.1:8080',capabilities:['read','work.v1']};
 case 'request_read_access':return {read_context:'consumer-read',expires_at:Math.floor(Date.now()/1000)+600};
 case 'read':if(message.request?.kind!=='get_work')throw Error('unexpected request');return {stream_id:'consumer-stream',status:200,content_type:'application/json',content_encoding:'identity',content_length:String(bytes.length),limit:8388608,dispatched:false};
 case 'pull':return {stream_id:'consumer-stream',seq:0,total:bytes.length,chunk_b64:btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,''),done:true};
 case 'cancel':return {cancelled:true,dispatched:false};
 case 'dispose':return {disposed:true};
 default:throw Error('unsupported fixture action');
 }
}};
async function main(){
 const runtime=await initialize({wasm:wasmUrl});
 const connection=await connectCastalia();
 const client=runtime.createClient({connection});
 const read_context=await connection.requestReadAccess();
 const work=await client.getWork('Issue','example',{read_context});
 const todoBody=${JSON.stringify(JSON.stringify(todoFixture.result.work))};
 const generic=runtime.connectDevgraph({origin:location.origin,readCredential:'fixture-read',fetch:async(input,options)=>{
   if(new Headers(options?.headers).get('authorization')!=='Bearer fixture-read')throw Error('read credential missing');
   return new Response(String(input).includes('/todos/')?todoBody:body,{headers:{'content-type':'application/json'}});
 }});
 const genericWork=await generic.getWork('Issue','example');
 const todo=await generic.getTodo('Todo','example');
 const progress:'not_started'|'in_progress'|'done'|null=todo.progress;
 if(genericWork.version!==9223372036854775807n||progress!=='not_started')throw Error('generic read failed');
 // Type-check the explicit setup controls without invoking them in this read fixture.
 const prepareSetup:()=>Promise<{approve():Promise<{state:'approved'}>;connect():Promise<{state:'connected'}>}> = () => generic.prepareWalletSetup();
 void prepareSetup;
 const genericPrepared=generic.prepare({schema:'devgraph.work-request.v2',operation:'create',kind:'Todo',id:'example',expected_version:null,payload:{id:'example',title:'Example'}},{idempotency_key:'generic-consumer-request-0001'});
 const canonical:Uint8Array=genericPrepared.canonical_bytes;
 if(!canonical.byteLength)throw Error('generic typed bytes missing');
 genericPrepared.dispose();generic.dispose();
 if(work.version!==9223372036854775807n||work.priority!==-9223372036854775808n)throw Error('lossless read failed');
 const prepared=client.prepare({schema:'devgraph.work-request.v1',operation:'patch',kind:'Issue',id:'example',expected_version:1n,payload:{title:'Updated'}},{idempotency_key:'consumer-operation-0001'});
 if(!(prepared.canonical_bytes instanceof Uint8Array))throw Error('owned bytes missing');
 let rejected=false;
 try{client.prepare({schema:'devgraph.work-request.v1',operation:'patch',kind:'Issue',id:'example',expected_version:9007199254740992n,payload:{title:'Updated'}},{idempotency_key:'consumer-operation-0002'})}catch{rejected=true}
 if(!rejected)throw Error('untyped unsafe BigInt accepted');
 prepared.dispose();client.dispose();runtime.dispose();await connection.dispose();
 result.textContent=exportJson({passed:true,version:work.version,priority:work.priority,wasm_url:wasmUrl,scope:'installed tarball + fixture transport; native provider excluded'});
}
void main().catch(error=>{result.textContent=JSON.stringify({passed:false,error:String(error)})});
`);

const ssr=run(resolve(consumer,'ssr.mjs')).trim();
const esm=run(resolve(consumer,'plain-esm.mjs')).trim();
const types=run(resolve(consumer,'node_modules/typescript/bin/tsc'),['--noEmit']);
const build=run(resolve(consumer,'node_modules/vite/bin/vite.js'),['build']);
const built=resolve(consumer,'dist');
const violations=[];
const server=createServer(async(req,res)=>{
  const path=new URL(req.url,'http://localhost').pathname;
  const file=resolve(built,'.'+(path==='/'?'/index.html':path));
  if(!file.startsWith(built+'/')){res.writeHead(404);res.end();return;}
  try{
    res.setHeader('content-security-policy',"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'");
    res.setHeader('content-type',extname(file)==='.wasm'?'application/wasm':extname(file)==='.html'?'text/html':'text/javascript');
    res.end(await readFile(file));
  }catch{res.writeHead(404);res.end()}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const engines=[];
try{
  for(const [name,engine] of Object.entries({chromium,firefox,webkit})){
    const browser=await engine.launch({headless:true});
    try{
      const page=await browser.newPage();
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      page.on('console',message=>{if(message.type()==='error')violations.push({name,message:message.text()})});
      const response=await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.waitForFunction(()=>document.querySelector('#result')?.textContent?.startsWith('{'));
      const result=JSON.parse(await page.locator('#result').textContent());
      assert.equal(result.passed,true,JSON.stringify(result));assert.deepEqual(errors,[]);
      assert.equal(result.version,'9223372036854775807');
      assert.match(response.headers()['content-security-policy'],/wasm-unsafe-eval/);
      assert.ok(!response.headers()['content-security-policy'].includes("'unsafe-eval'"));
      engines.push({name,browser_version:browser.version(),result});
    }finally{await browser.close()}
  }
}finally{await new Promise(resolve=>server.close(resolve))}
assert.deepEqual(violations,[],'browser console errors/CSP violations');
const evidence={scope:'External installed tarball; fixture transport only, no native/provider authority claim',
  source_commit:sourceManifest.source_commit,consumer_directory:consumer,node:process.version,typescript:'5.9.3',vite:'7.3.5',playwright:'1.61.1',
  package:packageJson.name,version:packageJson.version,tarball_sha256:hash(tarball),
  wasm_sha256:hash(installedWasm),runtime_js_sha256:hash(Buffer.concat(await Promise.all(runtimeFiles.map(file=>readFile(resolve(installed,'dist',file)))))),
  installation:'npm install --ignore-scripts; only SDK tarball plus pinned TypeScript/Vite dependencies',
  no_lifecycle_downloads:true,ssr,esm,types:{passed:true,positive_and_expected_error_assertions:true,compiler_output:types},
  vite:{passed:true,output:build.trim(),assets:await readdir(resolve(built,'assets'))},engines};
await mkdir(resolve(root,'.sdk-validation'),{recursive:true});
await writeFile(resolve(root,'.sdk-validation/installed-consumers.json'),JSON.stringify(evidence,null,2)+'\n');
console.log(JSON.stringify({ssr,esm,types:'passed',vite:'passed',engines:engines.map(({name})=>name),tarball_sha256:evidence.tarball_sha256}));
