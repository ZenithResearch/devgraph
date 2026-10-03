export const KINDS=['Proposal','Initiative','Project','Issue','Task'];
export const COLUMNS=['backlog','planning','plan_approval','ready','in_progress','review','done','waiting'];
export const FILTER_KEY='devgraph.kanban.filters.v1';
export const DEFAULTS={q:'',scope:'',descendants:false,kind:'',workflow:'',stage:'',column:'',archived:'exclude'};
export function filters(value={}) {
 const result={...DEFAULTS};
 if(!value||typeof value!=='object')return result;
 for(const key of Object.keys(result))if(typeof value[key]===typeof result[key])result[key]=value[key];
 if(!KINDS.includes(result.kind))result.kind='';
 if(!['','vibe-ceo.v1','execution.v1','unset'].includes(result.workflow))result.workflow='';
 if(!['',...COLUMNS].includes(result.column))result.column='';
 if(!['exclude','include','only'].includes(result.archived))result.archived='exclude';
 if(!/^(?:(?:Initiative|Project|Issue)\/[a-z0-9][a-z0-9-]*)?$/.test(result.scope))result.scope='';
 if(!/^[a-z_]{0,64}$/.test(result.stage))result.stage='';
 result.q=result.q.slice(0,200);return result;
}
export function query(value,extra={}){const params=new URLSearchParams();for(const [k,v]of Object.entries({...filters(value),...extra}))if(v!==''&&v!==false&&v!==null&&v!==undefined)params.set(k,String(v));return params.toString();}
export function safeUrl(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
export function wireJSON(value){if(typeof value==='bigint')return value.toString();if(Array.isArray(value))return '['+value.map(wireJSON).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+wireJSON(value[k])).join(',')+'}';return JSON.stringify(value);}
export function request(card,operation,payload){if(!/^[1-9]\d{0,18}$/.test(card.version)||BigInt(card.version)>9007199254740991n)throw Error('Refresh the item before changing it.');return wireJSON({schema:'devgraph.work-request.v1',operation,kind:card.kind,id:card.id,expected_version:BigInt(card.version),payload});}
export function appendPage(current,incoming){const known=new Set(current.map(x=>x.key));return [...current,...incoming.filter(x=>!known.has(x.key))];}
