// Optional browser qualification against the synthetic demo on port 4175.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {writeFileSync,mkdirSync} from 'node:fs';
const require=createRequire(process.env.DEVGRAPH_PLAYWRIGHT_PACKAGE || import.meta.url);
const {chromium}=require('playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const output=process.env.DEVGRAPH_QA_OUTPUT || join(tmpdir(),'devgraph-topology-qa');mkdirSync(output,{recursive:true});
await page.goto('http://127.0.0.1:4175');await page.locator('#token').fill('fake-credential-monitor');await page.locator('#refresh-rate').selectOption('0');await page.getByRole('button',{name:'Connect',exact:true}).click();
await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===4&&!graphView.worker);
await page.locator('#graph-filters').evaluate(el=>el.open=true).catch(async()=>{await page.getByText('Filters',{exact:true}).click()});
await page.locator('#facet-work_kind').getByRole('button',{name:'None',exact:true}).click();
await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===1);
await page.locator('[data-graph-category=observation]').uncheck();
await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===0);
assert.match(await page.locator('#scene-empty').textContent(),/No items match/);
await page.locator('#graph-clear-filters').click();await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===4);
for(const cat of ['arena','observation','receipt'])await page.locator(`[data-graph-category=${cat}]`).uncheck();
await page.locator('#facet-work_kind').getByRole('button',{name:'None',exact:true}).click();await page.locator('#facet-work_kind input[value=Issue]').check();
await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===1&&state.snapshot.graph_nodes[0].kind==='Issue');
await page.locator('#observer-toggle').click();
await page.reload();await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===1&&!graphView.worker);
assert.equal(await page.locator('#observer-toggle').getAttribute('aria-expanded'),'false');
assert.deepEqual(await page.evaluate(()=>graphView.filters.work_kind),['Issue']);
const preferences=await page.evaluate(()=>JSON.parse(localStorage.getItem(graphView.preferenceKey)));
assert.ok(!JSON.stringify(preferences).includes('fake-credential'));
await page.locator('#graph-clear-filters').click();await page.waitForFunction(()=>state.snapshot?.counts?.matching_nodes===4&&!graphView.worker);
await page.locator('#graph-svg').scrollIntoViewIfNeeded();
const node=page.locator('.graph-node').first();await node.hover();await page.waitForTimeout(80);
assert.match(await page.locator('#graph-preview').innerText(),/connected items/);
const key=await node.getAttribute('data-node-key');
const before=await page.evaluate(key=>({...graphView.nodePositions.get(key)}),key);
const box=await node.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+55,box.y+box.height/2+30,{steps:5});await page.mouse.up();
assert.notDeepEqual(await page.evaluate(key=>({...graphView.nodePositions.get(key)}),key),before);
await page.locator('#graph-undo').click();assert.deepEqual(await page.evaluate(key=>({...graphView.nodePositions.get(key)}),key),before);
await page.locator('#graph-reset').click();await page.waitForFunction(()=>!graphView.worker);await page.locator('#graph-undo').click();assert.deepEqual(await page.evaluate(key=>({...graphView.nodePositions.get(key)}),key),before);
const dimensions=[];
for(const width of [1280,1440,1920,760]) {
 await page.setViewportSize({width,height:1000});await page.evaluate(()=>{document.getElementById('graph-details').classList.remove('collapsed');document.getElementById('reader-toggle').setAttribute('aria-expanded','true');document.getElementById('reader-toggle').textContent='Hide details';});
 await page.waitForTimeout(100);
 dimensions.push(await page.evaluate(()=>({viewport:innerWidth,graph:document.getElementById('graph-svg').clientWidth,details:document.getElementById('graph-details').clientWidth,columns:getComputedStyle(document.querySelector('.graph-layout')).gridTemplateColumns,overflow:document.documentElement.scrollWidth>innerWidth})));
 assert.equal(dimensions.at(-1).overflow,false);
 await page.locator('#topology').screenshot({path:`${output}/layout-${width}.png`});
}
await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>{document.getElementById('graph-details').classList.add('collapsed');document.getElementById('graph-result-list').open=false;});
const performanceResults=[];
for(const cpu of [1,4]) {
 const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setCPUThrottlingRate',{rate:cpu});
 for(const size of [250,1500]) {
  const result=await page.evaluate(async size=>{
   invalidateLayout();pauseGraphOrbit();graphView.initialArranged=true;graphView.fitted=true;graphView.labels='selected';graphView.hoveredKey=null;state.selectedGraphKey=null;graphView.visibleCategories=new Set(Topology.categories);graphView.filters=Topology.filters();
   const kinds=['Proposal','Initiative','Project','Issue','Task'];const nodes=Array.from({length:size},(_,i)=>({key:`${kinds[i%5]}:bench-${i}`,kind:kinds[i%5],id:`bench-${i}`,title:`Work item ${i}`,status:'draft',category:'work',archived:false}));
   const edges=[];for(let i=1;i<size;i++)for(const d of [1,7,29])if(i>=d)edges.push({source:nodes[i-d].key,target:nodes[i].key,relationship:'HAS_CHILD'});
   state.snapshot={graph_nodes:nodes,graph_edges:edges};graphView.nodePositions.clear();graphView.nodeVelocities.clear();
   const start=performance.now();updateGraphVisibility();const initialMs=performance.now()-start;
   const draw=[],frames=[];let previous=await new Promise(requestAnimationFrame);
   for(let i=0;i<45;i++){const at=await new Promise(requestAnimationFrame);frames.push(at-previous);previous=at;const t=performance.now();graphView.yaw+=.004;renderGraph(nodes,edges);draw.push(performance.now()-t);}
   const workerStart=performance.now();relaxGraph(40,null);while(graphView.worker)await new Promise(r=>setTimeout(r,10));const workerMs=performance.now()-workerStart;
   const percentile=(xs,p)=>[...xs].sort((a,b)=>a-b)[Math.floor(xs.length*p)];
   return {nodes:size,edges:edges.length,renderer:graphView.canvasMode?'canvas':'svg',initialMs,drawMedianMs:percentile(draw,.5),drawP95Ms:percentile(draw,.95),frameP95Ms:percentile(frames,.95),workerCompletionMs:workerMs,svgElements:document.querySelectorAll('#graph-svg *').length};
  },size);
  performanceResults.push({cpuSlowdown:cpu,...result});
 }
 await cdp.detach();
}
await page.locator('#graph-svg').scrollIntoViewIfNeeded();
const hit=await page.evaluate(()=>{const rect=graphSvg.getBoundingClientRect();const [key,p]=[...graphView.projected].find(([,p])=>p.x>100&&p.x<graphView.width-100&&p.y>100&&p.y<graphView.height-100);return {key,x:rect.x+p.x,y:rect.y+p.y};});
await page.mouse.move(hit.x,hit.y);await page.waitForTimeout(100);assert.ok((await page.locator('#graph-preview').innerText()).length>0);
await page.mouse.click(hit.x,hit.y);await page.waitForTimeout(100);
assert.ok(await page.evaluate(()=>!!state.selectedGraphKey||!document.getElementById('node-chooser').hidden));
await page.locator('#topology').screenshot({path:`${output}/large-map.png`});
assert.deepEqual(errors,[]);
const report={deviceScaleFactor:2,browser:await browser.version(),viewport:'1440x1000',fixture:'5 Work kinds, edges at offsets 1/7/29; fitted overview; 45 camera frames',dimensions,performanceResults,checks:['API-backed filters','empty selections','reload restoration','Observer collapse','hover labels','drag undo','reset undo','responsive layout','canvas picking','no page errors']};
writeFileSync(`${output}/browser-qa.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));await browser.close();
