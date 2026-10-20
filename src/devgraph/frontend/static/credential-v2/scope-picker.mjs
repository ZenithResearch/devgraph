const plain=value=>!!value&&Object.getPrototypeOf(value)===Object.prototype;
const ranks=['Arena','Initiative','Project'];
export function validPath(value){
 if(!plain(value)||value.schema!=='devgraph.resource-path.v1'||Object.keys(value).sort().join(',')!=='path,schema'||!Array.isArray(value.path)||!value.path.length||value.path.length>3)return false;
 let prior=-1;for(const anchor of value.path){if(typeof anchor!=='string'||!/^(Arena|Initiative|Project)\/[a-z0-9](?:[a-z0-9-]{0,254}[a-z0-9])?$/.test(anchor))return false;const rank=ranks.indexOf(anchor.split('/')[0]);if(rank<=prior)return false;prior=rank;}return true;
}
export function validateCatalog(value,origin){
 if(!plain(value)||value.schema!=='devgraph.access-catalog.v1'||value.receiver!==origin||typeof value.issuer!=='string'||!Number.isSafeInteger(value.revision)||value.revision<1||!/^[a-f0-9]{64}$/.test(value.catalog_digest_sha256)||!Array.isArray(value.presets)||value.presets.length>16||!Array.isArray(value.scopes??[])||(value.scopes??[]).length>2000)throw Error('invalid_access_catalog');
 const seen=new Set();for(const p of value.presets){if(!plain(p)||Object.keys(p).sort().join(',')!=='id,name,read_operations,version,write_operations'||typeof p.id!=='string'||!/^[a-z][a-z0-9-]{0,63}$/.test(p.id)||seen.has(p.id)||typeof p.name!=='string'||!p.name.length||p.name.length>80||!Number.isSafeInteger(p.version)||p.version<1||!Array.isArray(p.read_operations)||!Array.isArray(p.write_operations)||p.read_operations.length>5||p.write_operations.length>64||new Set(p.read_operations).size!==p.read_operations.length||new Set(p.write_operations).size!==p.write_operations.length||p.read_operations.some(op=>!['list','get','relationships','children','blockers'].includes(op))||p.write_operations.some(op=>typeof op!=='string'||!/^devgraph\.(?:work\.[a-z.]+\.v2|arena\.[a-z.]+\.v1)$/.test(op)||op.length>128))throw Error('invalid_access_catalog');seen.add(p.id);}
 if(new Set((value.scopes??[]).map(r=>r.resource)).size!==(value.scopes??[]).length)throw Error('invalid_access_catalog');
 for(const row of value.scopes??[])if(!plain(row)||Object.keys(row).sort().join(',')!=='parent,resource,resource_path,title'||!validPath(row.resource_path)||row.resource!==row.resource_path.path.at(-1)||typeof row.title!=='string'||row.title.length>4096)throw Error('invalid_access_catalog');
 return structuredClone(value);
}
export function preferenceFor(catalog,presetID,resourcePaths=[]){
 if(resourcePaths.length>16||new Set(resourcePaths.map(p=>JSON.stringify(p))).size!==resourcePaths.length||resourcePaths.some(p=>!validPath(p)||!(catalog.scopes??[]).some(r=>JSON.stringify(r.resource_path)===JSON.stringify(p))))throw Error('invalid_resource_path');
 const preset=catalog.presets.find(p=>p.id===presetID);
 if(presetID&&!preset)throw Error('preset_unavailable');
 return {catalog_digest_sha256:catalog.catalog_digest_sha256,id:preset?.id??null,version:preset?.version??null,...(resourcePaths.length?{resource_paths:structuredClone(resourcePaths)}:{})};
}
const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
/** One picker shared by the requester and operator; no authority is produced. */
export function mountScopePicker(element,{catalog,scopes=catalog.scopes??[],operator=false,presetID='',paths=[],onChange=()=>{}}){
 catalog=validateCatalog({...catalog,scopes},catalog.receiver);
 let committed=[],current=paths[0]??null,disposed=false;
 const field=make('fieldset'),legend=make('legend',operator?'Preset and area':'What access do you need?');field.append(legend);
 const roleLabel=make('label','Preset'),role=make('select');role.setAttribute('aria-label','Access preset');role.append(new Option(operator?'Custom permissions':'No preset · Let the operator choose',''),...catalog.presets.map(p=>new Option(p.name,p.id)));role.value=presetID;roleLabel.append(role);field.append(roleLabel);
 const areaLabel=make('label','Area'),area=make('select');area.setAttribute('aria-label','Scope selection');area.append(new Option(operator?'All resources permitted by policy':'Let the operator choose the area','all'),new Option('Choose Arena, Initiative or Project','path'));area.value=current?'path':'all';areaLabel.append(area);field.append(areaLabel);
 const levels=make('div');levels.className='scope-picker-levels';const selects={};
 for(const kind of ranks){const label=make('label',kind),select=make('select');select.setAttribute('aria-label',kind+' scope');selects[kind]=select;label.append(select);levels.append(label);}field.append(levels);
 const chips=make('ul'),add=make('button','Keep this area and add another');add.type='button';add.className='secondary';field.append(chips,add);
 const help=make('p','Includes the selected container and resources beneath it at each access. New descendants are included; resources moved outside the path lose access.');help.className='muted';field.append(help);
 const summary=make('p');summary.className='scope-picker-summary';field.append(summary);element.replaceChildren(field);
 function value(){return {presetID:role.value,paths:area.value==='path'?[...committed,...(current&&!committed.some(p=>JSON.stringify(p)===JSON.stringify(current))?[current]:[])]:[],all:area.value==='all'};}
 function emit(){if(disposed)return;const v=value(),preset=catalog.presets.find(p=>p.id===v.presetID);summary.textContent=(preset?.name??(operator?'Custom permissions':'Operator chooses permissions'))+' · '+(v.paths.length?v.paths.map(p=>p.path.join(' → ')).join('; '):!v.all?'Choose at least one area':operator?'All matching resources within policy':'Operator chooses the area');onChange(v);}
 function refresh(from){
  if(from==='Arena'){selects.Initiative.value='';selects.Project.value='';}if(from==='Initiative')selects.Project.value='';
  const arena=selects.Arena.value,initiative=selects.Initiative.value;
  for(const kind of ranks){const select=selects[kind],old=select.value;let rows=scopes.filter(r=>r.resource.startsWith(kind+'/'));
   if(kind!=='Arena'&&arena)rows=rows.filter(r=>arena==='standalone'?!r.resource_path.path.some(a=>a.startsWith('Arena/')):r.resource_path.path.includes(arena));
   if(kind==='Project'&&initiative)rows=rows.filter(r=>r.resource_path.path.includes(initiative));
   select.replaceChildren(new Option(kind==='Arena'?'Choose an Arena':'All within selected area',''),...(kind==='Arena'?[new Option('Standalone work','standalone')]:[]),...rows.map(r=>new Option(r.title,r.resource)));
   if([...select.options].some(o=>o.value===old))select.value=old;
   select.disabled=area.value!=='path'||!rows.length&&kind!=='Arena';
  }
  const key=selects.Project.value||selects.Initiative.value||(selects.Arena.value==='standalone'?'':selects.Arena.value);
  current=scopes.find(row=>row.resource===key)?.resource_path??null;
  levels.hidden=area.value!=='path';help.hidden=area.value!=='path';add.hidden=!current;add.disabled=committed.length>=15;chips.replaceChildren();
  for(const [i,p]of committed.entries()){const row=make('li',p.path.join(' → ')),remove=make('button','Remove');remove.type='button';remove.onclick=()=>{committed.splice(i,1);refresh();};row.append(remove);chips.append(row);}emit();
 }
 role.onchange=emit;area.onchange=()=>refresh();for(const kind of ranks)selects[kind].onchange=()=>refresh(kind);
 add.onclick=()=>{if(committed.length<15&&current&&!committed.some(p=>JSON.stringify(p)===JSON.stringify(current)))committed.push(structuredClone(current));current=null;for(const select of Object.values(selects))select.value='';refresh();};
 if(current){for(const anchor of current.path)selects[anchor.split('/')[0]].append(new Option(anchor,anchor));for(const anchor of current.path)selects[anchor.split('/')[0]].value=anchor;committed=paths.slice(1);}
 refresh();return {value,dispose(){disposed=true;element.replaceChildren();}};
}
