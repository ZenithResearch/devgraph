const kinds=new Set(['Todo','Proposal','Initiative','Project','Issue','Task','Arena','Decision','ReviewPacket','Handoff','ExternalLink']);
export function resourceIds(text){
 const ids=typeof text==='string'?text.split(/[\s,]+/).filter(Boolean):text;
 if(!Array.isArray(ids)||ids.length>256||ids.some(id=>typeof id!=='string'||!kinds.has(id.split('/')[0])||!new RegExp('^[A-Za-z]+/[a-z0-9][a-z0-9-]{0,255}$').test(id)))throw Error('invalid_resource_selection');
 return [...new Set(ids)].sort();
}
const intersect=(a,b)=>b.filter(x=>!a||a.includes(x));
export function makeSelection({ceiling,ceilingDigest,recipient,mode='read',operations=[],workIds=[],arenaIds=[],expiresAt,now}){
 if(!['read','write','both'].includes(mode)||!Number.isSafeInteger(expiresAt)||expiresAt<=now||expiresAt>ceiling.not_after)throw Error('invalid_expiry');
 workIds=resourceIds(workIds);
 if(arenaIds.some(id=>typeof id!=='string'||!/^[a-z0-9][a-z0-9-]{0,255}$/.test(id)))throw Error('invalid_arena_selection');
 arenaIds=[...new Set(arenaIds)].sort();
 if(arenaIds.length&&mode!=='read'&&ceiling.schema!=='devgraph.access-draft.v3')throw Error('arena_scoped_writes_unsupported');
 if(ceiling.subject_public_key&&ceiling.subject_public_key!==recipient.subject_public_key)throw Error('recipient_outside_policy');
 const base={...structuredClone(ceiling),subject_public_key:recipient.subject_public_key,not_before:Math.max(now,ceiling.not_before),not_after:expiresAt,previous_digest:ceilingDigest};
 const selected={};
 if(mode!=='write'){
  const read={...base,schema:'devgraph.access-draft.v2',installation_id:recipient.installation_id};delete read.write_grants;
  read.read_grants=(ceiling.read_grants??[]).map(raw=>{
   const g=structuredClone(raw);
   if(workIds.length){g.work_ids=intersect(g.work_ids,workIds).filter(id=>g.work_kinds.includes(id.split('/')[0]));if(!g.work_ids.length)return null;}
   if(arenaIds.length){g.arena_ids=intersect(g.arena_ids,arenaIds);if(!g.arena_ids.length)return null;}
   return g;
  }).filter(Boolean);
  if(!read.read_grants.length)throw Error('read_scope_unavailable');selected.read=read;
 }
 if(mode!=='read'){
  if(ceiling.installation_id)throw Error('installation_scoped_writes_unsupported');
  const write={...base};delete write.read_grants;delete write.installation_id;
  write.write_grants=(ceiling.write_grants??[]).filter(g=>operations.includes(g.operation)).map(raw=>{
   const g=structuredClone(raw);
   if(arenaIds.length){g.arena_ids=intersect(g.arena_ids,arenaIds);if(!g.arena_ids.length)return null;}
   if(workIds.length)g.resources=g.resources.map(r=>({...r,ids:intersect(r.ids,workIds.filter(id=>id.startsWith(r.kind+'/')).map(id=>id.slice(r.kind.length+1)))})).filter(r=>r.ids.length);
   return g.resources.length?g:null;
  }).filter(Boolean);
  if(!write.write_grants.length)throw Error('write_scope_unavailable');selected.write=write;
 }
 return selected;
}
export function selectionSummary(selection){
 const parts=[];
 if(selection.read){const grants=selection.read.read_grants;parts.push({title:'Read',expires_at:selection.read.not_after,binding:'This member and Wallet installation',operations:[...new Set(grants.flatMap(g=>g.operations))],resources:grants.map(g=>({operations:g.operations,types:g.work_kinds,objects:g.work_ids??'All matching current and future objects',arenas:g.arena_ids??'All permitted Arenas',archived:g.include_archived}))});}
 if(selection.write)parts.push({title:'Write',expires_at:selection.write.not_after,grants:selection.write.write_grants.map(g=>({operation:g.operation,resources:g.resources,arenas:g.arena_ids??'All permitted Arenas'})),binding:'This member; not restricted to one installation',operations:selection.write.write_grants.map(g=>g.operation),resources:selection.write.write_grants.flatMap(g=>g.resources.map(r=>r.kind+'/'+(r.ids?.join(', ')??'all matching current and future objects')))});
 return parts;
}
