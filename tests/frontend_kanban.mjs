import test from 'node:test';
import assert from 'node:assert/strict';
import {filters,query,request,appendPage,safeUrl} from '../src/devgraph/frontend/static/kanban/core.mjs';
import {Wallet} from '../src/devgraph/frontend/static/kanban/wallet.mjs';
const raw=request({kind:'Task',id:'test-one',version:'7'},'workflow.transition',{stage:'intake',reason:''});
const key='stable-recovery-key-1234';
test('only view filters persist and exact versions remain lossless',()=>{
 const f=filters({scope:'Task/no',kind:'Artifact',credential:'never-save',authorization:'never-save',q:'x'.repeat(500)});
 assert.equal(f.scope,'');assert.equal(f.kind,'');assert.equal(f.q.length,200);assert.equal('credential' in f,false);
 assert.equal(query({scope:'Initiative/example',descendants:true,column:'review'}),'scope=Initiative%2Fexample&descendants=true&column=review&archived=exclude');
 assert.equal(JSON.parse(raw).expected_version,7);
 assert.throws(()=>request({kind:'Task',id:'x',version:'9007199254740992'},'workflow.transition',{}));
 assert.equal(safeUrl('javascript:alert(1)'),null);assert.equal(safeUrl('https://user@example.test'),null);
 assert.deepEqual(appendPage([{key:'a'}],[{key:'a'},{key:'b'}]),[{key:'a'},{key:'b'}]);
});
test('legacy native bridge is not admitted as generic approval support',async()=>{
 const calls=[];const wallet=new Wallet({devgraph:async m=>{calls.push(m.action);}});
 await assert.rejects(wallet.prepareSetup(),/credential-bound approvals/);
 assert.equal(wallet.connection,null);assert.deepEqual(calls,[]);
});
