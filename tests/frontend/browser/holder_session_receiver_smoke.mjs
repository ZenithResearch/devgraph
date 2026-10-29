/** Matched actual WASM/HTTPS receiver qualification; disposable data only. */
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import https from 'node:https';
import {createBrowserHolderSession} from '../../../src/devgraph/frontend/static/credential-v2/holder-session.mjs';
const receiver=resolve(process.env.DEVGRAPH_RECEIVER_SOURCE_DIRECTORY??fileURLToPath(new URL('../../../',import.meta.url)));
const walletRoot=resolve(process.env.CASTALIA_WALLET_SOURCE_DIRECTORY??'');
assert.ok(process.env.CASTALIA_WALLET_SOURCE_DIRECTORY,'Pass the reviewed canonical Wallet checkout');
const python=process.env.DEVGRAPH_TEST_PYTHON;
assert.ok(python,'Pass the qualified Python environment');
for(const name of ['SECS_HOLDER_TEST_BINARY','SECS_READ_TEST_BINARY'])assert.ok(process.env[name],'Pass '+name);
const {initSync,WasmWallet}=await import(pathToFileURL(join(walletRoot,'apps/chrome-extension/src/generated/castalia-wallet-core/bindings.js')));
const {CASTALIA_WALLET_CORE_WASM}=await import(pathToFileURL(join(walletRoot,'apps/chrome-extension/src/generated/castalia-wallet-core/wasm-bytes.js')));
const {createHolderSessionExchange}=await import(pathToFileURL(join(walletRoot,'apps/chrome-extension/src/holder-session-exchange.ts')));
initSync({module:CASTALIA_WALLET_CORE_WASM});
const wallet=new WasmWallet(),password='Disposable matched session qualification password';
const custody=wallet.create(password,1n),origin='https://graph.example',installation='33'.repeat(16),now=()=>Math.floor(Date.now()/1000);
const directory=await mkdtemp(join(tmpdir(),'devgraph-matched-holder-session-'));
const fixtureDirectory=join(directory,'issuer');
const env={...process.env,PYTHONPATH:join(receiver,'src')+':'+receiver,PYTHONDONTWRITEBYTECODE:'1'};
let server,lines,agent;
try{
const created=spawnSync(python,['-m','tests.native_read.holder_session_fixture','--directory',fixtureDirectory,'--holder-public-key',wallet.publicKey(),'--holder-binary',process.env.SECS_HOLDER_TEST_BINARY,'--read-binary',process.env.SECS_READ_TEST_BINARY,'--origin',origin],{cwd:receiver,env,encoding:'utf8'});
if(created.status!==0)throw Error('fixture creation failed: '+String(created.error?.message??created.stderr).slice(-4000));
const metadata=JSON.parse(created.stdout.trim());
server=spawn(python,['-m','tests.native_read.holder_session_fixture','--profile-file',metadata.profile,'--https'],{cwd:receiver,env,stdio:['ignore','pipe','pipe']});
let diagnostics='';server.stderr.on('data',b=>diagnostics=(diagnostics+b).slice(-4000));
lines=createInterface({input:server.stdout});
const ready=await Promise.race([new Promise((accept,reject)=>{lines.once('line',line=>{try{accept(JSON.parse(line));}catch(error){reject(error);}});server.once('error',reject);server.once('exit',code=>reject(Error('fixture server exited '+code+': '+diagnostics)));}),new Promise((_,reject)=>{const timer=setTimeout(()=>reject(Error('fixture startup timeout')),15000);timer.unref();})]);
const ca=await readFile(ready.certificate),parent=JSON.parse(await readFile(metadata.parent,'utf8'));
let approvals=0,exchanges=0,reads=0,statuses=0;
agent=new https.Agent({ca,keepAlive:true});
async function request(path,{body,headers={},signal}={}){
 const raw=body===undefined?undefined:JSON.stringify(body);
 return new Promise((accept,reject)=>{
  const req=https.request(new URL(path,ready.base_url),{agent,method:body===undefined?'GET':'POST',headers:{...(raw===undefined?{}:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(raw)}),...headers},signal},res=>{
   let bytes=0;const chunks=[];
   res.on('data',b=>{bytes+=b.length;if(bytes>131072){req.destroy(Error('fixture response too large'));return;}chunks.push(b);});
   res.on('end',()=>accept(new Response(Buffer.concat(chunks),{status:res.statusCode,headers:res.headers})));
  });req.on('error',reject);req.end(raw);
 });
}
const invoke=(method,...args)=>JSON.parse(wallet[method](...args.map(x=>typeof x==='string'?x:JSON.stringify(x))));
const pins=[{issuer:metadata.provider.issuer,key_id:metadata.provider.key_id,public_key:metadata.provider.public_key,audience:'devgraph',callers:[{kind:'browser',id:origin}]}];
const stored=invoke('reviewHolderGrant',parent,pins,{kind:'browser',id:origin},installation,String(now())).stored;
const adapter=createHolderSessionExchange({now,randomId:()=>crypto.randomUUID().replaceAll('-',''),
 grants:{prepare:async()=>({stored,revision:1}),assertRevision:async r=>assert.equal(r,1)},route:()=>'',audience:()=>true,
 review:(s,c,o,a,i,n)=>invoke('reviewHolderSessionAuthorization',s,c,o,a,i,String(n)),
 approve:(s,c,signature,o,a,i,n)=>{approvals++;return invoke('approveHolderSessionAuthorization',s,c,signature,o,a,i,String(n));},
 verify:(p,c,expected,n)=>invoke('verifyHolderSessionCredential',p,c,expected,String(n)),
 fetchImpl:async(url,options)=>{
  const u=new URL(String(url));assert.equal(u.origin,origin);assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');
  if(u.pathname.endsWith('/exchange'))exchanges++;
  return request(u.pathname,{body:options.body?JSON.parse(options.body):undefined,headers:{Origin:'chrome-extension://disposable-test'},signal:options.signal});
 },
});
const profile={presentations:pins};
async function open(ttl){
 const session=await createBrowserHolderSession({origin,service:metadata.provider.service,holderPublicKey:wallet.publicKey(),installationId:installation,profile,ttlSeconds:ttl,now});
 const prepared=await adapter.prepare({service:metadata.provider.service,origin,installation,sessionPublicKey:session.publicKey,sessionInstallationId:session.installationId,ttlSeconds:ttl,permissions:null});
 const signature=await session.prepare(prepared.challenge),opened=await adapter.activate(prepared,signature);
 assert.equal('parent_grant' in opened,false);await session.accept(opened.credential);
 return{session,credential:opened.credential,expiry:prepared.challenge.expires_at};
}
const call={operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph',parameters:{path:'/todos/v2?kind=Task'}};
async function read(session){const envelope=await session.present(call);const result=await request('/monitor/wallet-access/v1/requests',{body:envelope,headers:{Origin:origin}});reads++;return{result,envelope};}
 let available=false;
 for(let n=0;n<40;n++){try{available=(await request('/holder-sessions/v1/capabilities')).ok;}catch{}if(available)break;await new Promise(r=>setTimeout(r,50));}
 assert.ok(available,'actual HTTPS fixture must be ready');
 const first=await open(8);wallet.lock();
 const a=await read(first.session);assert.equal(a.result.status,200);assert.deepEqual((await a.result.json()).items.map(v=>v.key),['Task/allowed-1']);
 const b=await read(first.session);assert.equal(b.result.status,200);assert.notEqual(a.envelope.proof.nonce,b.envelope.proof.nonce);
 assert.equal((await request('/monitor/wallet-access/v1/requests',{body:a.envelope,headers:{Origin:origin}})).status,401);
 const proof=await first.session.status(),original=await request('/holder-sessions/v1/status',{body:proof});statuses++;
 assert.equal(original.status,200);const recovered=await original.json();assert.equal(recovered.state,'issued');assert.equal(recovered.expires_at,first.expiry);assert.deepEqual(recovered.credential,first.credential);
 assert.equal(invoke('verifyHolderSessionCredential',metadata.provider,recovered.credential,first.credential.claims.challenge,String(now())).expires_at,first.expiry);
 const mutation=await first.session.present({...call,operation:'devgraph.work.patch'});
 assert.ok([401,403].includes((await request('/monitor/wallet-access/v1/requests',{body:mutation,headers:{Origin:origin}})).status));
 const expires=first.expiry;while(now()<expires)await new Promise(r=>setTimeout(r,100));
 assert.equal(first.session.active,false);await assert.rejects(first.session.present(call));
 assert.equal((await request('/monitor/wallet-access/v1/requests',{body:b.envelope,headers:{Origin:origin}})).status,401);
 wallet.unlock(new TextEncoder().encode(custody),password);
 const second=await open(60);wallet.lock();assert.equal((await read(second.session)).result.status,200);
 await writeFile(join(fixtureDirectory,'REVOKE'),'synthetic fixture revocation\n',{mode:0o600});
 assert.equal((await read(second.session)).result.status,401);
 const revoked=await request('/holder-sessions/v1/status',{body:await second.session.status()});statuses++;
 assert.equal(revoked.status,200);const result=await revoked.json();assert.equal(result.state,'revoked');assert.equal(result.credential,null);assert.equal(result.expires_at,second.expiry);
 second.session.dispose();assert.equal(approvals,2);assert.equal(exchanges,2);
 console.log(JSON.stringify({qualification:'actual Wallet WASM/private exchange/browser-key module + matching HTTPS receiver',synthetic_data:true,explicit_session_approvals:approvals,exchanges,reads,statuses,read_after_wallet_lock:true,exact_scoped_results:true,original_status_only:true,replay_denied:true,write_intent_denied:true,hard_expiry:true,current_parent_revocation:true,production_gate_changed:false}));
}finally{
 agent?.destroy();wallet.lock();wallet.free();lines?.close();
 if(server?.pid&&server.exitCode===null){server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>{const timer=setTimeout(()=>{server.kill('SIGKILL');r();},3000);timer.unref();})]);
 }
 await rm(directory,{recursive:true,force:true});
}
