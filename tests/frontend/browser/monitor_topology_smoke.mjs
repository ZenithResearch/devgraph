/** Real public UI + scoped projection, synthetic controller only. No personal browser. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const repo=resolve(fileURLToPath(new URL('../../../',import.meta.url)));
const require=createRequire(process.env.DEVGRAPH_PLAYWRIGHT_PACKAGE??new URL('../../../packages/web/package.json',import.meta.url));
const {chromium}=require('playwright-core');
const fixture=JSON.parse(execFileSync(process.env.DEVGRAPH_PYTHON??'python3',
 ['tests/frontend/browser/monitor_topology_fixture.py'],
 {cwd:repo,env:{...process.env,PYTHONPATH:join(repo,'src')+':'+repo},encoding:'utf8',maxBuffer:16*1024*1024}));
const output=resolve(process.env.DEVGRAPH_UI_EVIDENCE??'/private/tmp/devgraph-topology-ui');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.DEVGRAPH_CHROMIUM_EXECUTABLE});
const results=[];
try{
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];let revalidations=0;
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());assert.equal(url.origin,'https://graph.example');
   if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
   if(url.pathname==='/monitor')return route.fulfill({status:200,headers:fixture.shell.headers,body:fixture.shell.body});
   if(url.pathname==='/monitor/topology-assets/session-access.js')return route.fulfill({contentType:'text/javascript',body:`
    globalThis.DevgraphWalletAccess={mount(element,{onChange}){
     const controller={connected:true,topologyRevalidation:true,sessionKey:'synthetic',preferenceSeed:'synthetic',disconnect(){},
      async read(path){const response=await fetch('/fixture-read?target='+encodeURIComponent(path));
       if(!response.ok)throw Object.assign(Error('read_not_granted'),{code:'read_not_granted',status:403});return response.json();}};
     element.querySelector('[data-session-status]').textContent='Signed in · synthetic QA';
     queueMicrotask(()=>onChange(controller));return controller;
    }};`});
   if(url.pathname==='/fixture-read'){
    const target=url.searchParams.get('target').split('?')[0];let data=fixture.reads[target];
    if(target==='/monitor/topology/v1' && new URL(url.searchParams.get('target'),url.origin).searchParams.has('if_revision')){data=fixture.reads.conditional;revalidations++;}
    return route.fulfill({status:data?200:403,contentType:'application/json',body:JSON.stringify(data??{error:'read_not_granted'})});
   }
   const name=url.pathname.replace('/monitor/topology-assets/','');
   assert.match(name,/^[a-zA-Z0-9.-]+$/);
   const body=await readFile(join(repo,'src/devgraph/frontend/static/topology',name));
   return route.fulfill({contentType:name.endsWith('.css')?'text/css':'text/javascript',body});
  });
  await page.goto('https://graph.example/monitor');
  await page.getByText('Graph ready',{exact:true}).waitFor();
  await page.locator('a[href="#topology"]').click();
  const label=await page.locator('#graph-meta').textContent();
  assert.match(label,/6 neighborhoods/);assert.doesNotMatch(label,/0 connections/);
  assert.equal(await page.locator('#graph-arena option').count(),8);
  assert.equal(await page.locator('#graph-svg').getByText(/Arena not shared or unassigned/).count(),0);
  const before=await page.locator('#graph-svg').innerHTML();
  const revalidated=page.waitForResponse(response=>response.url().includes('/fixture-read?target=')&&decodeURIComponent(response.url()).includes('if_revision='));
  await page.getByRole('button',{name:'Refresh now',exact:true}).click();
  await revalidated;
  await page.waitForFunction(()=>!document.getElementById('status-line').textContent.includes('Loading graph'));
  assert.equal(await page.locator('#graph-svg').innerHTML(),before,'unchanged projection preserves graph DOM');
  assert.deepEqual(errors,[]);
  await page.screenshot({path:join(output,`topology-${width}.png`),fullPage:true});
  assert.ok(revalidations>0);
  results.push({width,label,page_errors:errors,revalidations,
    full_bytes:JSON.stringify(fixture.reads['/monitor/topology/v1']).length,
    conditional_bytes:JSON.stringify(fixture.reads.conditional).length});await context.close();
 }
 await writeFile(join(output,'verification.json'),JSON.stringify({synthetic_only:true,work_items:1183,results},null,2));
 console.log(JSON.stringify(results));
}finally{await browser.close();}
