import test from 'node:test';
import {runtimeFiles} from '../../scripts/artifact-files.mjs';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {gzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {chromium,firefox,webkit} from 'playwright';

const root=resolve(fileURLToPath(new URL('../../../../',import.meta.url)));
const dist=resolve(root,'packages/web/dist');
const protocolCorpus=await readFile(resolve(root,'tests/fixtures/sdk-work-v1/requests.json'));
const adversarialCorpus=await readFile(resolve(root,'tests/fixtures/sdk-work-v1/adversarial.json'));
const browserTest=await readFile(fileURLToPath(import.meta.url));
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const vectors=JSON.parse(protocolCorpus);
const adversarial=JSON.parse(adversarialCorpus);
const records=[];
// Served as a same-origin module Worker; no blob URL, eval, or Wallet provider.
// Keeping the fixture in this file binds it to browser_test_sha256.
const workerFixture=`
import {initialize} from '/index.js';
let runtime;
const probe=value=>{try{value.createClient({connection:{}});return 'unexpected_client'}catch(error){return error.code}};
self.onmessage=async({data})=>{
  try{
    if(data==='initialize'){
      const emptyModule=await WebAssembly.compile(new Uint8Array([0,97,115,109,1,0,0,0]));
      let wrongArtifact;
      try{await initialize({wasm:emptyModule})}catch(error){wrongArtifact=error.code}
      if(wrongArtifact!=='initialization_failed')throw Error('valid wrong WASM artifact was accepted');
      runtime=await initialize({wasm:new URL('/internal/devgraph_web_bg.wasm',self.location.href)});
      self.postMessage({ok:true,wrong_artifact:wrongArtifact,live:probe(runtime)});
    }else if(data==='reinitialize'){
      const prior=runtime;
      const before=probe(prior);
      runtime=await initialize({wasm:new URL('/internal/devgraph_web_bg.wasm',self.location.href)});
      prior.dispose();
      self.postMessage({ok:true,before,prior:probe(prior),live:probe(runtime)});
    }else if(data==='dispose'){
      runtime.dispose();self.postMessage({ok:true,disposed:probe(runtime)});
    }else throw Error('unexpected Worker action');
  }catch(error){self.postMessage({ok:false,error:String(error)})}
};
`;

for(const [name,engine] of Object.entries({chromium,firefox,webkit}))test(`${name}: actual WASM, raw corpus, BigInt, init and resource measurements`,async()=>{
  const server=createServer(async(req,res)=>{
    const path=new URL(req.url,'http://localhost').pathname;
    if(path==='/'){res.setHeader('content-type','text/html');res.end('<!doctype html><title>SDK core qualification</title>');return;}
    if(path==='/worker-fixture.js'){res.setHeader('content-type','text/javascript');res.end(workerFixture);return;}
    const file=resolve(dist,'.'+path);
    if(!file.startsWith(dist+'/')){res.writeHead(404);res.end();return;}
    try{const body=await readFile(file);res.setHeader('cache-control','no-store');res.setHeader('content-type',extname(file)==='.wasm'?'application/wasm':'text/javascript');res.end(body)}catch{res.writeHead(404);res.end()}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await engine.launch({headless:true,...(name==='chromium'?{args:['--js-flags=--expose-gc']}:{})});
    const page=await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const result=await page.evaluate(async({vectors,adversarial,name})=>{
      const sdk=await import('/index.js');
      const bindings=await import('/internal/devgraph_web.js');
      const module=await bindings.default();
      const ensure=(condition,message)=>{if(!condition)throw Error(message)};
      const rejects=fn=>{try{fn();return false}catch{return true}};
      const key='browser-protocol-123456';
      for(const vector of vectors){const p=bindings.prepare_bytes(new TextEncoder().encode(vector.raw),key);ensure(new TextDecoder().decode(p.canonical_bytes())===vector.canonical,vector.name);ensure(p.summary().request_digest_sha256===vector.digest,'digest');p.free()}
      let negatives=0;
      for(const vector of adversarial){
        const raw=vector.raw;
        if(typeof raw!=='string')continue;
        const expected=vector.rust_accept;
        if(expected===false){ensure(rejects(()=>bindings.prepare_bytes(new TextEncoder().encode(raw),key)),'adversarial '+vector.name);negatives++}
      }
      const request=JSON.parse(vectors[3].raw);
      for(const value of [9007199254740992n,-9007199254740992n,NaN,Infinity,1.2,-0])ensure(rejects(()=>bindings.prepare_value({...request,payload:{...request.payload,priority:value}},key)),'original numeric ABI');
      for(const title of ['\ud800','a\udc00'])ensure(rejects(()=>bindings.prepare_value({...request,payload:{...request.payload,title}},key)),'UTF16');
      const metadata={status:200,content_type:'application/json',content_encoding:'identity'};
      const raw=new TextEncoder().encode('{"id":"i-1","kind":"Issue","title":"t","description":"","status":"draft","version":9223372036854775807,"priority":-9223372036854775808,"artifact_ids":[],"external_link_ids":[]}');
      const work=bindings.decode_read(raw,metadata,{kind:'get_work',work_kind:'Issue',id:'i-1'});
      ensure(work.version===9223372036854775807n&&work.priority===-9223372036854775808n,'lossless domain');
      ensure(Array.isArray(work.artifact_ids)&&Object.getPrototypeOf(work)===Object.prototype,'plain result');
      const invalid=new TextEncoder().encode('{"id":"i-1","id":"i-2"}');ensure(rejects(()=>bindings.decode_read(invalid,metadata,{kind:'get_work',work_kind:'Issue',id:'i-1'})),'duplicate raw response');
      let failed=false;try{await sdk.initialize({wasm:new Uint8Array([0,1])})}catch(e){failed=e.code==='initialization_failed'}ensure(failed,'invalid module');
      const concurrent=await Promise.all(Array.from({length:8},()=>sdk.initialize()));concurrent.forEach(r=>r.dispose());
      const suppliedBytes=new Uint8Array(await (await fetch('/internal/devgraph_web_bg.wasm')).arrayBuffer());
      const compiled=await WebAssembly.compile(suppliedBytes);
      const cold=[],warm=[];
      for(let i=0;i<30;i++){let start=performance.now();let runtime=await sdk.initialize({wasm:suppliedBytes});cold.push(performance.now()-start);runtime.dispose();start=performance.now();runtime=await sdk.initialize({wasm:compiled});warm.push(performance.now()-start);runtime.dispose()}
      const p95=a=>a.toSorted((a,b)=>a-b)[Math.ceil(a.length*.95)-1];
      // One owned 1 MiB page repeatedly crosses the actual WASM decoder. Drop each result.
      const pageInput={kind:'list_work',work_kind:'Issue',limit:1,filters:{include_archived:false,descending:false}};
      const pageRaw=new TextEncoder().encode(JSON.stringify({items:[{id:'i-1',kind:'Issue',title:'t',description:'x'.repeat(1024*1024),status:'draft',version:1,priority:0,artifact_ids:[],external_link_ids:[]}]}));
      bindings.decode_read(pageRaw,metadata,pageInput);globalThis.gc?.();
      const baseline=module.memory.buffer.byteLength;
      const heapBefore=performance.memory?.usedJSHeapSize??null;
      for(let i=0;i<1000;i++){bindings.decode_read(pageRaw,metadata,pageInput);if(i%50===0){globalThis.gc?.();await new Promise(r=>setTimeout(r,0))}}
      globalThis.gc?.();await new Promise(r=>setTimeout(r,0));
      const growth=module.memory.buffer.byteLength-baseline;
      const heapAfter=performance.memory?.usedJSHeapSize??null;
      // Maximum default page, below the wire ceiling including its JSON envelope.
      const large=new TextEncoder().encode(JSON.stringify({items:[{id:'i-1',kind:'Issue',title:'t',description:'x'.repeat(16*1024*1024-1024),status:'draft',version:1,priority:0,artifact_ids:[],external_link_ids:[]}]}));
      bindings.decode_read(large,metadata,pageInput);
      const peakWasm=module.memory.buffer.byteLength;
      for(let i=0;i<100;i++){const runtime=await sdk.initialize();runtime.dispose()}
      return {name,vectors:vectors.length,negative_vectors:negatives,supplied_bytes_p95_ms:p95(cold),compiled_module_p95_ms:p95(warm),wasm_retained_growth_bytes:growth,js_retained_growth_bytes:heapAfter==null?null:heapAfter-heapBefore,large_wasm_high_water_bytes:peakWasm};
    },{vectors,adversarial,name});
    assert.ok(result.supplied_bytes_p95_ms<=500,JSON.stringify(result));assert.ok(result.compiled_module_p95_ms<=100,JSON.stringify(result));assert.ok(result.wasm_retained_growth_bytes<=16*1024*1024,JSON.stringify(result));assert.ok(result.large_wasm_high_water_bytes<=256*1024*1024,JSON.stringify(result));
    result.worker_smoke=await page.evaluate(async()=>{
      const first=new Worker('/worker-fixture.js',{type:'module'});
      const second=new Worker('/worker-fixture.js',{type:'module'});
      const call=(worker,action)=>new Promise((resolve,reject)=>{
        const finish=(error,value)=>{clearTimeout(timer);worker.removeEventListener('message',message);worker.removeEventListener('error',failure);error?reject(error):resolve(value)};
        const message=event=>finish(event.data.ok?null:Error(event.data.error),event.data);
        const failure=event=>finish(Error(event.message));
        const timer=setTimeout(()=>finish(Error('Worker smoke timed out')),10000);
        worker.addEventListener('message',message);worker.addEventListener('error',failure);worker.postMessage(action);
      });
      const ensure=(value,message)=>{if(!value)throw Error(message)};
      try{
        const initialized=await Promise.all([call(first,'initialize'),call(second,'initialize')]);
        ensure(initialized.every(value=>value.wrong_artifact==='initialization_failed'&&value.live==='invalid_connection'),'Worker initialization/artifact check');
        ensure((await call(first,'dispose')).disposed==='disposed','first Worker disposal');
        first.terminate();
        const survivor=await call(second,'reinitialize');
        ensure(survivor.before==='invalid_connection'&&survivor.prior==='disposed'&&survivor.live==='invalid_connection','Worker instances are not independent');
        ensure((await call(second,'dispose')).disposed==='disposed','second Worker disposal');
        return {workers:2,wrong_valid_wasm_rejected:true,successful_retry:true,independent_disposal_and_termination:true,scope:'package initialization and ownership only; Wallet provider excluded'};
      }finally{first.terminate();second.terminate()}
    });
    if(name==='chromium'){
      // A fresh document isolates initialization retention from large codec buffers.
      const memoryPage=await browser.newPage();
      await memoryPage.goto(`http://127.0.0.1:${server.address().port}/`);
      const cdp=await memoryPage.context().newCDPSession(memoryPage);
      await memoryPage.evaluate(async()=>{
        const sdk=await import('/index.js');
        const bytes=await (await fetch('/internal/devgraph_web_bg.wasm')).arrayBuffer();
        const compiled=await WebAssembly.compile(bytes);
        const memories=[];
        const instantiate=WebAssembly.instantiate.bind(WebAssembly);
        WebAssembly.instantiate=async(...args)=>{
          const result=await instantiate(...args);
          const instance=result instanceof WebAssembly.Instance?result:result.instance;
          if(instance.exports.memory instanceof WebAssembly.Memory)
            memories.push({ref:new WeakRef(instance.exports.memory),bytes:instance.exports.memory.buffer.byteLength});
          return result;
        };
        globalThis.__sdkMemoryProbe={sdk,compiled,memories};
        for(let i=0;i<10;i++){const runtime=await sdk.initialize({wasm:compiled});runtime.dispose()}
      });
      const collect=async()=>{
        for(let i=0;i<3;i++){
          await cdp.send('HeapProfiler.collectGarbage');
          await memoryPage.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));
        }
      };
      await collect();
      const before=await cdp.send('Runtime.getHeapUsage');
      await memoryPage.evaluate(async()=>{
        const {sdk,compiled,memories}=globalThis.__sdkMemoryProbe;
        memories.length=0;
        for(let i=0;i<100;i++){const runtime=await sdk.initialize({wasm:compiled});runtime.dispose()}
      });
      await collect();
      const after=await cdp.send('Runtime.getHeapUsage');
      const retention=await memoryPage.evaluate(()=>{
        const memories=globalThis.__sdkMemoryProbe.memories;
        const live=memories.filter(item=>item.ref.deref());
        return {custom_cycles:memories.length,retained_wasm_instances:live.length,
                retained_wasm_bytes:live.reduce((sum,item)=>sum+item.ref.deref().buffer.byteLength,0)};
      });
      const jsGrowth=Math.max(0,after.usedSize-before.usedSize);
      const backingGrowth=typeof after.backingStorageSize==='number'&&typeof before.backingStorageSize==='number'
        ?Math.max(0,after.backingStorageSize-before.backingStorageSize):null;
      result.custom_initialization_retention={...retention,js_heap_growth_bytes:jsGrowth,
        backing_storage_growth_bytes:backingGrowth,combined_js_and_tracked_wasm_growth_bytes:jsGrowth+retention.retained_wasm_bytes,
        cdp_heap_before:before,cdp_heap_after:after};
      assert.equal(retention.custom_cycles,100,'instrument every custom instance');
      assert.equal(retention.retained_wasm_instances,0,'disposed custom WASM instances must be collectible');
      assert.ok(jsGrowth+retention.retained_wasm_bytes<=16*1024*1024,JSON.stringify(result));
      if(backingGrowth!==null)assert.ok(jsGrowth+backingGrowth<=16*1024*1024,JSON.stringify(result));
      await cdp.detach();await memoryPage.close();
    }
    records.push({...result,browser_version:browser.version()});
    console.log(JSON.stringify(result));
  }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
});

test.after(async()=>{
  const wasm=await readFile(resolve(dist,'internal/devgraph_web_bg.wasm'));
  const js=Buffer.concat(await Promise.all(runtimeFiles.map(file=>readFile(resolve(dist,file)))));
  assert.ok(gzipSync(wasm).length<=1024*1024);assert.ok(gzipSync(js).length<=64*1024);
  assert.ok(gzipSync(wasm).length+gzipSync(js).length<=1024*1024);
  const result={scope:'core codecs and browser package only; native provider route is qualified separately',
    node:process.version,playwright:'1.61.1',wasm_sha256:createHash('sha256').update(wasm).digest('hex'),
    runtime_js_sha256:createHash('sha256').update(js).digest('hex'),
    protocol_corpus_sha256:hash(protocolCorpus),adversarial_corpus_sha256:hash(adversarialCorpus),
    browser_test_sha256:hash(browserTest),
    initialization_measurement:'30 supplied-byte initializations and 30 reused compiled-module initializations; network excluded',
    wasm_gzip_bytes:gzipSync(wasm).length,js_gzip_bytes:gzipSync(js).length,engines:records};
  await mkdir(resolve(root,'.sdk-validation'),{recursive:true});await writeFile(resolve(root,'.sdk-validation/browser-core.json'),JSON.stringify(result,null,2)+'\n');
});
