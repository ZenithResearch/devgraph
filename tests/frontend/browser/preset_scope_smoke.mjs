/** Synthetic UI only. Real contract/activation is separately exercised in Python. */
import {readFile,writeFile,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';
import {chromium} from '../../../packages/web/node_modules/playwright/index.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),base=join(root,'src/devgraph/frontend/static'),out=process.env.DEVGRAPH_PRESET_UI_OUTPUT??'/private/tmp/devgraph-preset-ui-20261020';await mkdir(out,{recursive:true});
const standing=process.env.DEVGRAPH_STANDING_GRANT==='1';
const origin='https://example.test',catalog=JSON.parse(await readFile(join(root,'docs/contracts/access-catalog/v1/catalog.example.json'))),now=Math.floor(Date.now()/1000),key='a'.repeat(64),device='b'.repeat(32),id='c'.repeat(32),digest='d'.repeat(64),actions=[];
const ceiling={schema:'devgraph.access-draft.v4',issuer:'fixture',receiver:origin,write_audience:'devgraph://receiver-local',not_before:now-10,not_after:now+365*86400,lifetime_seconds:300,read_grants:[{scope:'devgraph.work.read',operations:['get','list'],relationship_types:['parent'],work_kinds:['Task','Project'],include_archived:false}],write_grants:[{operation:'devgraph.work.patch.v2',resources:[{kind:'Task'}]},{operation:'devgraph.work.create.v2',resources:[{kind:'Task'}]}]};
if(standing){ceiling.schema='devgraph.access-draft.v5';ceiling.validity={schema:'castalia.grant-validity.v1',mode:'until_revoked',not_before:now-10};delete ceiling.not_before;delete ceiling.not_after;}
// Additional published roots exercise unions, not an invented broad grant.
for(const [kind,id,title,path] of [
 ['Arena','b','B',['Arena/b']],
 ['Initiative','root-b','Second initiative',['Arena/b','Initiative/root-b']],
 ['Project','project-b','Second project',['Arena/b','Initiative/root-b','Project/project-b']]
])catalog.scopes.push({resource:`${kind}/${id}`,title,parent:path.length>1?path.at(-2):null,resource_path:{schema:'devgraph.resource-path.v1',path}});
const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
const browser=await chromium.launch({headless:true,...(process.env.DEVGRAPH_BROWSER_EXECUTABLE?{executablePath:process.env.DEVGRAPH_BROWSER_EXECUTABLE}:{})});
try{
 const page=await browser.newPage({viewport:{width:1280,height:1000},timezoneId:'America/Vancouver'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',async route=>{const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();const send=(v,type='application/json')=>route.fulfill({status:200,contentType:type,body:typeof v==='string'?v:JSON.stringify(v)});
 if(url.pathname==='/monitor/access/')return send((await readFile(join(base,'access/index.html'),'utf8')).replace('__WALLET_CONNECTION__','<div id="wallet-read-connection"></div>'),'text/html');
 if(url.pathname==='/monitor/topology-assets/wallet-access.js')return send(`globalThis.DevgraphWalletAccess={mount(el,options){queueMicrotask(options.onChange);return {requestIdentity:{subject_public_key:'${key}',installation_id:'${device}'},approveAccess:async challenge=>({challenge,proof:{},signature:'synthetic'})}}};`,'text/javascript');
 const dirs={'/monitor/access-assets/':'access','/monitor/topology-assets/':'topology','/assets/credential-v2/':'credential-v2'};for(const[prefix,dir]of Object.entries(dirs))if(url.pathname.startsWith(prefix))return send(await readFile(join(base,dir,url.pathname.split('/').at(-1)),'utf8'),url.pathname.endsWith('.css')?'text/css':'text/javascript');
 const body=req.postDataJSON();if(url.pathname.endsWith('/challenges'))return send({schema:'castalia.access-request-challenge.v1',receiver:origin,subject_public_key:key,installation_id:device,expires_at:now+120,details:{action:body.action}});
 const a=body.challenge.details.action;actions.push(a);const result={schema:'devgraph.access-operator-result.v2',action_id:a.action_id,authority_issued:false};
 if(a.action==='inbox')return send({...result,ceiling,ceiling_digest_sha256:digest,catalog,scopes:catalog.scopes,activation:{schema:'devgraph.access-activation-capabilities.v3',read:true,write:true,mixed:true,read_action_schema:standing?'devgraph.access-operator-action.v6':'devgraph.access-operator-action.v4',write_action_schema:standing?'devgraph.access-operator-action.v7':'devgraph.access-operator-action.v5'},inbox:{total:1,next_cursor:null,items:[{request_id:id,subject_public_key:key,installation_id:device,request_digest_sha256:digest,version:1,received_at:now,previous_assignment_digest:null,previous_write_assignment_digest:null,intent:{schema:'devgraph.access-intent.v2',preference:{id:'contributor',version:1,catalog_digest_sha256:catalog.catalog_digest_sha256,resource_paths:[catalog.scopes.find(s=>s.resource.startsWith('Project/')).resource_path]}}}]}});
 if(a.action==='review')return send({...result,request_id:id,expected_version:1,request_digest_sha256:digest,review_id:a.action_id,selection:a.selection,selection_digest_sha256:digest,plans:{activation:{schema:'devgraph.mixed-activation-plan.v1',assignment_digest:createHash('sha256').update(stable(a.selection)).digest('hex'),previous_assignment_digest:null,previous_write_assignment_digest:null}}});
 if(a.action==='approve')return send({...result,request_id:id,request_digest_sha256:digest,version:2,status:'active',activation:{active:true,credential_issued:false,access_mode:'read_write',assignment_digest:createHash('sha256').update(stable(a.selection)).digest('hex')}});
 throw Error('Unexpected synthetic operation '+a.action);
 });
 const consent=async()=>{await page.locator('#confirm:not([hidden])').click();};
 await page.goto(origin+'/monitor/access/');await page.locator('#refresh').click();await consent();await page.locator('#requests button').click();
 await page.getByRole('combobox',{name:'Access preset',exact:true}).waitFor();assert.equal(await page.locator('#mode').inputValue(),'both');assert.equal(await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).isChecked(),true);
 assert.equal(await page.locator('#op-list input[value="devgraph.work.create.v2"]').isChecked(),false);
 await page.getByRole('combobox',{name:'Access preset',exact:true}).selectOption('project-manager');
 await page.getByRole('checkbox',{name:'Initiative: Fixture',exact:true}).check();
 assert.equal(await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).isChecked(),true);
 assert.equal(await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'Initiative: Fixture',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).check();
 // Multiple Arenas and Initiatives form a union; inherited children stay disabled.
 await page.getByRole('checkbox',{name:'Arena: A',exact:true}).check();
 await page.getByRole('checkbox',{name:'Arena: B',exact:true}).check();
 assert.match(await page.locator('.scope-picker-summary').textContent(),/Arena: A; Arena: B/);
 await page.getByRole('checkbox',{name:'Arena: A',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Arena: B',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Initiative: Fixture',exact:true}).check();
 if(await page.getByRole('button',{name:'Expand Arena: B',exact:true}).count())await page.getByRole('button',{name:'Expand Arena: B',exact:true}).click();
 await page.getByRole('checkbox',{name:'Initiative: Second initiative',exact:true}).check();
 assert.match(await page.locator('.scope-picker-summary').textContent(),/Second initiative/);
 await page.getByRole('checkbox',{name:'Initiative: Fixture',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Initiative: Second initiative',exact:true}).uncheck();
 await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).check();
 // Visual navigation must not reset manually narrowed operations.
 await page.locator('#advanced-scope>summary').click();
 const patch=page.locator('#op-list input[value="devgraph.work.patch.v2"]');await patch.uncheck();
 await page.getByRole('searchbox',{name:'Find resources',exact:true}).fill('project-a');
 assert.equal(await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).isChecked(),true);
 assert.equal(await patch.isChecked(),false);
 await page.getByRole('searchbox',{name:'Find resources',exact:true}).fill('');assert.equal(await patch.isChecked(),false);await patch.check();
 await page.getByRole('radio',{name:'Global · All resources permitted by policy',exact:true}).check();
 assert.match(await page.locator('.scope-picker-summary').textContent(),/Global within operator policy/);
 await page.getByRole('radio',{name:'Selected areas · Choose one or more subtrees',exact:true}).check();
 assert.equal(await page.getByRole('checkbox',{name:'Project: Fixture',exact:true}).isChecked(),true);
 assert.equal(await page.locator('#validity').inputValue(),'');
 await page.getByRole('button',{name:'Review selection',exact:true}).click();assert.equal(actions.filter(a=>a.action==='review').length,0);
 await page.locator('#validity').selectOption(standing?'until_revoked':'policy');assert.match(await page.locator('#validity-note').textContent(),/Read credentials last up to 300 seconds/);
 await page.locator('#validity').selectOption('custom');const chosenDate=await page.evaluate(timestamp=>{const d=new Date(timestamp*1000);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,19);},now+30*86400);await page.locator('#expiry').fill(chosenDate);
 if(standing)await page.locator('#validity').selectOption('until_revoked');
 await page.screenshot({path:join(out,'picker-wide.png'),fullPage:true});
 await page.getByRole('button',{name:'Review selection',exact:true}).click();await consent();await page.locator('#selection-review').waitFor({state:'visible'});
 const review=actions.find(a=>a.action==='review');assert.equal(review.schema,standing?'devgraph.access-operator-action.v7':'devgraph.access-operator-action.v5');assert.equal(review.selection.read.preset.id,'project-manager');assert.deepEqual(review.selection.read.read_grants[0].resource_paths,[{schema:'devgraph.resource-path.v1',path:['Arena/a','Initiative/root-a','Project/project-a']}]);assert.equal(review.selection.write.write_grants.length,1);if(standing){assert.equal(review.selection.read.validity.mode,'until_revoked');assert.equal(review.selection.write.validity.mode,'until_revoked');assert.equal(Object.hasOwn(review.selection.read,'not_after'),false);assert.match(await page.locator('#selection-summary').textContent(),/Until revoked/);}else{assert.equal(review.selection.read.not_after,now+30*86400);assert.equal(review.selection.write.not_after,now+30*86400);}assert.equal(review.selection.read.lifetime_seconds,300);
 await page.setViewportSize({width:375,height:812});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:join(out,'exact-selection-narrow.png'),fullPage:true});
 await page.locator('#approve-selection').click();await consent();await page.getByRole('status').filter({hasText:'Read and write grants activated together'}).waitFor();assert.equal(actions.filter(a=>a.action==='approve').length,1);assert.deepEqual(errors,[]);
 await writeFile(join(out,'verification.json'),JSON.stringify({synthetic_ui_only:true,standing_until_revoked:standing,preset_and_path_preselected:true,preset_changed:true,explicit_grant_expiry_required:true,long_lived_grant_expiry_signed:true,credential_lifetime_preserved:true,exact_path_signed:true,mixed_review_schema:review.schema,unsupported_creation_excluded:true,approval_submissions:1,narrow_overflow:false,page_errors:errors},null,2));console.log('Preset and path picker browser walkthrough passed.');
}finally{await browser.close();}
