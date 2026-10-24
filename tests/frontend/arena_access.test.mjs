import {test} from 'node:test';
import assert from 'node:assert/strict';
import {makeSelection,selectionSummary} from '../../src/devgraph/frontend/static/access/core.mjs';
import {monitorReadOperation} from '../../src/devgraph/frontend/static/credential-v2/read-access.mjs';
import {policyView} from '../../src/devgraph/frontend/static/access/policy.mjs';
const ceiling={schema:'devgraph.access-draft.v6',issuer:'fixture',receiver:'https://graph.example',write_audience:'devgraph://receiver-local',validity:{schema:'castalia.grant-validity.v1',mode:'until_revoked',not_before:1},lifetime_seconds:900,read_grants:[{scope:'devgraph.work.read',operations:['list','get'],work_kinds:['Task'],relationship_types:['parent'],include_archived:false},{scope:'devgraph.arena.read',arena_ids:['allowed','second']}],write_grants:[]};
const choose=extra=>makeSelection({ceiling,ceilingDigest:'a'.repeat(64),recipient:{subject_public_key:'b'.repeat(64),installation_id:'c'.repeat(32)},validity:{...ceiling.validity,not_before:10},now:10,...extra});
test('Arena metadata is a separate explicit choice and never inferred from Work scope',()=>{
 assert.ok(choose({arenaIds:['allowed']}).read.read_grants.every(g=>g.scope==='devgraph.work.read'));
 const selected=choose({arenaMetadataIds:['allowed'],workIds:['Task/visible']});
 assert.deepEqual(selected.read.read_grants[1],{scope:'devgraph.arena.read',arena_ids:['allowed']});
 assert.deepEqual(selected.read.read_grants[0].work_ids,['Task/visible']);
 assert.throws(()=>choose({arenaMetadataIds:['private']}),/arena_metadata_scope_unavailable/);
 const view=policyView({ceiling});assert.deepEqual(view.arenaReadIds,['allowed','second']);
 assert.ok(selectionSummary(selected)[0].resources.some(r=>r.types.includes('Arena')));
});
test('browser transport admits only bounded Arena read route vocabulary',()=>{
 assert.equal(monitorReadOperation('/arenas?limit=2'),'devgraph.arena.list');
 assert.equal(monitorReadOperation('/arenas/allowed'),'devgraph.arena.get');
 assert.equal(monitorReadOperation('/arenas/allowed/members?limit=2'),'devgraph.arena.get');
 for(const target of ['/arenas/allowed/patch','/arenas%2fallowed','/arenas/allowed/../private'])assert.throws(()=>monitorReadOperation(target));
});
