// Repository-local, deterministic release authoring. Import updates are explicit pins.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {publishTree, generatedFile, safeRelative, verifyTree} from './lib/immutable-release.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const hash=b=>createHash('sha256').update(b).digest('hex');
const bytes=v=>Buffer.from(JSON.stringify(v,null,2)+'\n');
const read=n=>fs.readFile(path.join(root,safe(n)));
const safe=safeRelative;
const config=JSON.parse(await read('ontology/extension/publication.json'));
assert.equal(config.release,'v'+config.version);
assert.equal(new Set(config.files.map(f=>f.target)).size,config.files.length);
assert(!config.files.some(f=>f.target==='manifest.json'),'Reserved manifest target');
const ownership=JSON.parse(await read('ontology/ownership.json'));
assert.equal(ownership.schemaVersion,'devgraph.ontology-ownership.v1');
const core=config.imports.find(d=>d.ontologyId===ownership.canonicalCore.ontologyId);
assert(core,'Missing adopted core import');
for(const key of ['version','ontologyUrl','manifest'])assert.deepEqual(ownership.canonicalCore[key],core[key],'Ownership/import drift: '+key);
assert.match(ownership.canonicalCore.commit,/^[a-f0-9]{40}$/);
assert.equal(ownership.extension.ontologyId,config.ontology_id);
assert.equal(ownership.runtimeAdmission,false);
const packageFiles=new Map();
const files=new Map();
for(const f of config.files)files.set(safe(f.target),await read(f.source));
const digest=list=>hash(list.toSorted((a,b)=>a.path<b.path?-1:a.path>b.path?1:0).map(f=>`${f.sha256}  ${f.path}\n`).join(''));
for(const dependency of config.imports){
 const raw=await read(dependency.path);assert.equal(hash(raw),dependency.manifest.sha256,'Import manifest changed');assert.equal(raw.length,dependency.manifest.bytes);
 const manifest=JSON.parse(raw);assert.equal(manifest.ontology_id,dependency.ontologyId);assert.equal(manifest.authority.repository,ownership.canonicalCore.repository,'Ownership repository drift');assert.equal(manifest.version,dependency.version);assert.equal(digest(manifest.files),manifest.bundle_digest);
 const imported=new Map([['manifest.json',raw]]);
 for(const f of manifest.files){const b=await read(path.posix.join(path.posix.dirname(dependency.path),safe(f.path)));assert.equal(hash(b),f.sha256,'Import artifact changed');assert.equal(b.length,f.bytes);imported.set(f.path,b);}
 await verifyTree(path.join(root,path.posix.dirname(dependency.path)),imported);
 assert.equal(dependency.ontologyId,ownership.canonicalCore.ontologyId,'Unsupported unindexed import');
 for(const [name,b]of imported)packageFiles.set('core/v'+dependency.version+'/'+name,b);
}
const ontology=JSON.parse(files.get('ontology.jsonld')),metadata=ontology['@graph'].find(n=>n['@id']===config.ontology_id);
assert(metadata,'Missing authored ontology identity');assert.equal(metadata.versionInfo,config.version);assert.equal(metadata.canonicalAuthority,config.authority.repository);
assert.deepEqual((metadata['owl:imports']||[]).map(x=>x['@id']).sort(),config.imports.map(x=>x.ontologyUrl).sort(),'OWL imports and pinned dependency closure differ');
if(config.authority.name==='Zenith'){
 const original=JSON.parse(await read('imports/devgraph/v0.12.0/ontology.jsonld'));
 assert.deepEqual(ontology['@graph'].filter(n=>n['@id']!==config.ontology_id),original['@graph'].filter(n=>n['@id']!==config.ontology_id),'Core adoption changed source definitions or parent axioms');
 assert.deepEqual(JSON.parse(files.get('context.jsonld')),JSON.parse(await read('imports/devgraph/v0.12.0/context.jsonld')),'Core adoption changed term expansion');
}
const records=[...files].map(([name,b])=>({path:name,media_type:config.files.find(f=>f.target===name).media_type,bytes:b.length,sha256:hash(b),url:config.canonical_base_url+'/'+name})).sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
const manifest={schema_version:1,ontology_id:config.ontology_id,version:config.version,release:config.release,published_at:config.published_at,canonical_base_url:config.canonical_base_url,discovery_url:config.discovery_url,authority:config.authority,compatibility:config.compatibility,files:records,bundle_digest_algorithm:'sha256-file-list-v1',bundle_digest:digest(records),imports:config.imports.map(({path:_,...d})=>d)};
files.set('manifest.json',bytes(manifest));
const discovery={schema_version:1,ontology_id:config.ontology_id,canonical_authority:config.authority,current:{version:config.version,release:config.release,manifest_url:config.canonical_base_url+'/manifest.json',manifest_sha256:hash(files.get('manifest.json')),bundle_digest:manifest.bundle_digest,ontology_url:config.canonical_base_url+'/ontology.jsonld',context_url:config.canonical_base_url+'/context.jsonld'},supported_versions:[config.version],compatibility_policy:'Pin exact manifests and complete imports. Update each maintainer-owned source independently; retained releases are immutable.'};
const releaseRoot=path.join(root,'ontology/extension/releases',config.release);
const check=process.argv.includes('--check');
const runtimePath='ontology/releases/v0.12.0/manifest.json',runtimeRaw=await read(runtimePath),runtime=JSON.parse(runtimeRaw);
const extension={repository:config.authority.repository,ontologyId:config.ontology_id,version:config.version,manifest:{url:config.canonical_base_url+'/manifest.json',sha256:hash(files.get('manifest.json')),bytes:files.get('manifest.json').length,mediaType:'application/json'},ontologyUrl:config.canonical_base_url+'/ontology.jsonld'};
const runtimeCompatibility={ontologyId:runtime.ontology_id,version:runtime.version,manifest:{url:runtime.canonical_base_url+'/manifest.json',sha256:hash(runtimeRaw),bytes:runtimeRaw.length,mediaType:'application/json'},authority:runtime.authority};
const packagedIndex={schemaVersion:'devgraph.ontology-sources.v1',pathBase:'package-resources',adoptedCore:{...ownership.canonicalCore,resourcePath:'ontology-sources/core/v'+core.version},extension:{...extension,resourcePath:'ontology-sources/extension/'+config.release},runtimeCompatibility:{...runtimeCompatibility,resourcePath:'ontology-releases/'+runtime.release},runtimeAdmission:false,publicAvailability:'not-evaluated',websiteSelection:'not-evaluated'};
const sourceIndex={schemaVersion:'devgraph.ontology-source-index.v1',repository:config.authority.repository,sharedCoreAuthoring:ownership.canonicalCore.repository,adoptedCore:ownership.canonicalCore,extension,runtimeCompatibility,runtimeAdmission:false,legacyScope:'Historical issuer and packaged runtime compatibility; superseded only for shared-core authoring.',packagedIndex:'src/devgraph/resources/ontology-source-index.v1.json'};
await publishTree(releaseRoot,files,{check,boundary:root});
await generatedFile(path.join(root,'ontology/extension-discovery.json'),bytes(discovery),check);
await generatedFile(path.join(root,'ontology/source-index.v1.json'),bytes(sourceIndex),check);
await generatedFile(path.join(root,'src/devgraph/resources/ontology-ownership.json'),bytes(ownership),check);
for(const [name,b]of files)packageFiles.set('extension/'+config.release+'/'+name,b);
const packagedRoot=path.join(root,'src/devgraph/resources/ontology-sources');
if(check)await verifyTree(packagedRoot,packageFiles);
else{
 for(const [name,b]of packageFiles){const target=path.join(packagedRoot,name);await fs.mkdir(path.dirname(target),{recursive:true});await generatedFile(target,b,false);}
 await verifyTree(packagedRoot,packageFiles);
}
await generatedFile(path.join(root,'src/devgraph/resources/ontology-source-index.v1.json'),bytes(packagedIndex),check);
console.log(JSON.stringify({state:check?'verified':'generated',ontologyId:config.ontology_id,version:config.version,manifestSha256:hash(files.get('manifest.json')),bundleDigest:manifest.bundle_digest,definitions:ontology['@graph'].length-1,imports:config.imports.length}));
