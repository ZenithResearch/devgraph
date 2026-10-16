// Real browser gestures with a synthetic provider. Never an installed Wallet pass.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {chromium,firefox,webkit} from 'playwright';
const dist=fileURLToPath(new URL('../../dist/',import.meta.url));
for(const [name,engine] of Object.entries({chromium,firefox,webkit}))test(`${name}: provider setup preserves separate user gestures`,async()=>{
  const server=createServer(async(req,res)=>{
    const pathname=new URL(req.url,'http://localhost').pathname;
    if(pathname==='/'){
      res.setHeader('content-type','text/html');res.end('<!doctype html><button id="approve" disabled>Review provider</button><button id="connect" disabled>Connect Wallet</button><script type="module" src="/fixture.js"></script>');return;
    }
    if(pathname==='/fixture.js'){
      res.setHeader('content-type','text/javascript');res.end(`
        import {GenericClient} from '/generic.js';
        window.calls=[];window.problem=null;
        const record=action=>{if(!navigator.userActivation.isActive)throw Error('fresh click required');calls.push(action);};
        const provider={getCapabilities:async()=>['connection_v1','provider_profiles_v1','credential_presentation_v2'],
          requestConnection:()=>{record('connect');return Promise.resolve({state:'connected'});},
          proposeProviderProfile:()=>{record('approve');return Promise.resolve({state:'approved'});}};
        const client=new GenericClient({}, {provider});
        const setup=await client.prepareWalletSetup();
        document.querySelector('#approve').onclick=()=>setup.approve().catch(e=>window.problem=e.message);
        document.querySelector('#connect').onclick=()=>setup.connect().catch(e=>window.problem=e.message);
        document.querySelectorAll('button').forEach(b=>b.disabled=false);
      `);return;
    }
    if(pathname==='/credential-work/v2/provider-profile'){
      res.setHeader('content-type','application/json');
      const origin=`http://127.0.0.1:${server.address().port}`;
      res.end(JSON.stringify({schema:'castalia.provider-profile.v1',display_name:'Synthetic Devgraph',origins:[origin],membership:null,
        presentations:[{issuer:'secs:test',key_id:'test',public_key:'ab'.repeat(32),audience:'devgraph://receiver-local',callers:[{kind:'browser',id:origin}]}]}));return;
    }
    const file=resolve(dist,'.'+pathname);
    if(!file.startsWith(dist)){res.writeHead(404).end();return;}
    try{res.setHeader('content-type','text/javascript');res.end(await readFile(file));}
    catch{res.writeHead(404).end();}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
  try{
    browser=await engine.launch({headless:true});const page=await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(()=>!document.querySelector('#approve').disabled);
    assert.deepEqual(await page.evaluate(()=>window.calls),[]);
    await page.click('#approve');assert.deepEqual(await page.evaluate(()=>window.calls),['approve']);
    await page.click('#connect');assert.deepEqual(await page.evaluate(()=>window.calls),['approve','connect']);
    assert.equal(await page.evaluate(()=>window.problem),null);
  }finally{await browser?.close();await new Promise(r=>server.close(r));}
});
