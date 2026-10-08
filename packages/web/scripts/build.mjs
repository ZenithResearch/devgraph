import {execFileSync} from 'node:child_process';
import {runtimeFiles} from './artifact-files.mjs';
import {createHash} from 'node:crypto';
import {cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = resolve(here, '../..');
for (const name of ['LICENSE','NOTICE']) cpSync(resolve(root,name),resolve(here,name));
const packageVersion = JSON.parse(readFileSync(resolve(here,'package.json'),'utf8')).version;
const run = (program, args, options = {}) => execFileSync(program, args, {cwd:root,stdio:'inherit',...options});
const capture = (program, args) => run(program, args, {stdio:['ignore','pipe','inherit'],encoding:'utf8'}).trim();
const cli = process.env.WASM_BINDGEN ?? 'wasm-bindgen';
if (capture(cli, ['--version']) !== 'wasm-bindgen 0.2.127') throw Error('wasm-bindgen CLI must be exactly 0.2.127');
const metadata = JSON.parse(capture('cargo',['metadata','--locked','--format-version','1']));
const cargoWasm = resolve(metadata.target_directory,'wasm32-unknown-unknown/release/devgraph_web.wasm');
run('cargo', ['build','--locked','--release','--target','wasm32-unknown-unknown','-p','devgraph-web']);
const dist = resolve(here,'dist');
rmSync(dist,{recursive:true,force:true});
mkdirSync(resolve(dist,'internal'),{recursive:true});
run(cli, [cargoWasm,'--target','web','--out-dir',resolve(dist,'internal'),'--out-name','devgraph_web']);
// A static closure factory permits custom WASM instances to be garbage-collected.
// Unique import URLs would retain an instance forever in the ESM module cache.
const glue = readFileSync(resolve(dist,'internal/devgraph_web.js'),'utf8');
const exports = [...glue.matchAll(/^export (?:class|function) (\w+)/gm)].map(match=>match[1]);
const trailer = 'export { initSync, __wbg_init as default };';
if (/^import /m.test(glue) || !glue.includes(trailer) || exports.length !== 7)
  throw Error('Unexpected pinned bindgen glue; review the static factory transform');
const factory = 'export function createBindings() {\n'+glue
  .replaceAll(/^export (?=class|function)/gm,'')
  .replace(trailer,`return {${exports.join(',')},initSync,default:__wbg_init};`)
  +'\n}\n';
writeFileSync(resolve(dist,'internal/devgraph_web_factory.js'),factory);
writeFileSync(resolve(dist,'internal/devgraph_web_factory.d.ts'),'export declare function createBindings(): typeof import("./devgraph_web.js");\n');
cpSync(resolve(here,'src/index.js'),resolve(dist,'index.js'));
let genericSource=readFileSync(resolve(here,'src/generic.js'),'utf8');
for(const [source,target] of [
  ['credential-v2/coordinator.mjs','credential-coordinator.mjs'],
  ['credential-v2/transport.mjs','credential-transport.mjs'],
  ['credential-v2/subject.mjs','credential-subject.mjs'],
  ['credential-v2/provider-setup.mjs','credential-provider-setup.mjs'],
]) {
  cpSync(resolve(root,'src/devgraph/frontend/static',source),resolve(dist,'internal',target));
  genericSource=genericSource.replace('../../../src/devgraph/frontend/static/'+source,'./internal/'+target);
}
writeFileSync(resolve(dist,'generic.js'),genericSource);
// Types for the runtime facade are emitted from the same checked JavaScript.
// Bindgen's internal declarations are generated directly from Rust exports.
mkdirSync(resolve(here,'src/internal'),{recursive:true});
cpSync(resolve(dist,'internal/devgraph_web.d.ts'),resolve(here,'src/internal/devgraph_web.d.ts'));
cpSync(resolve(dist,'internal/devgraph_web_factory.d.ts'),resolve(here,'src/internal/devgraph_web_factory.d.ts'));
run('cargo',['run','--locked','-p','devgraph-client-core','--features','declarations','--bin','export-sdk-types','--',resolve(here,'src/domain.d.ts')]);
cpSync(resolve(here,'src/domain.d.ts'),resolve(dist,'domain.d.ts'));
run(resolve(here,'node_modules/.bin/tsc'), [], {cwd:here});
const gitPackages = metadata.packages.filter(p => p.source?.startsWith('git+'));
const webId = metadata.packages.find(p=>p.name==='devgraph-web').id;
const nodes = new Map(metadata.resolve.nodes.map(n=>[n.id,n]));
const runtimeIds = new Set();
const visit = id => {if(runtimeIds.has(id))return;runtimeIds.add(id);for(const dep of nodes.get(id).deps)if(dep.dep_kinds.some(k=>k.kind===null))visit(dep.pkg)};
visit(webId);
let notices = 'Third-party source notices for the application WASM runtime dependency closure.\n\n';
for(const pkg of metadata.packages.filter(p=>runtimeIds.has(p.id)&&p.source)){
  notices+=`${pkg.name} ${pkg.version} — ${pkg.license??'license unspecified'}\n${pkg.repository??''}\n`;
  const noticeRoots = new Set([dirname(pkg.manifest_path)]);
  if(pkg.source.startsWith('git+')) noticeRoots.add(capture('git',['-C',dirname(pkg.manifest_path),'rev-parse','--show-toplevel']));
  for(const noticeRoot of noticeRoots) for(const name of readdirSync(noticeRoot).filter(n=>/^(LICENSE|COPYING|NOTICE)(?:[.-]|$)/i.test(n))){
    notices+=`\n--- ${pkg.name}/${name} ---\n${readFileSync(resolve(noticeRoot,name),'utf8')}\n`;
  }
  notices+='\n';
}
writeFileSync(resolve(here,'THIRD-PARTY-NOTICES.txt'),notices);
const protocols = metadata.packages.filter(p => p.name === 'devgraph-work-protocol');
if (protocols.length !== 1 || protocols[0].source !== null
  || resolve(protocols[0].manifest_path) !== resolve(root,'crates/devgraph-work-protocol/Cargo.toml'))
  throw Error('SDK must build the canonical protocol in this public source revision');
const digest = file => createHash('sha256').update(readFileSync(file)).digest('hex');
const wasm = resolve(dist,'internal/devgraph_web_bg.wasm');
const lock = resolve(root,'Cargo.lock');
const runtimeDigest = createHash('sha256').update(Buffer.concat(runtimeFiles.map(file=>readFileSync(resolve(dist,file))))).digest('hex');
const evidenceInputs = {
  protocol_corpus_sha256:digest(resolve(root,'tests/fixtures/sdk-work-v1/requests.json')),
  adversarial_corpus_sha256:digest(resolve(root,'tests/fixtures/sdk-work-v1/adversarial.json')),
  browser_test_sha256:digest(resolve(here,'test/browser/core.test.mjs')),
};
let browserEvidence = null;
try {
  const evidence = JSON.parse(readFileSync(resolve(root,'.sdk-validation/browser-core.json'),'utf8'));
  if (evidence.wasm_sha256 === digest(wasm) && evidence.runtime_js_sha256 === runtimeDigest
    && Object.entries(evidenceInputs).every(([key,value])=>evidence[key]===value)
    && ['chromium','firefox','webkit'].every(name=>evidence.engines?.some(engine=>engine.name===name))) browserEvidence=evidence;
} catch { /* An untested build must never inherit qualification from another artifact. */ }
const manifest = {
  schema:'devgraph.sdk-build.v2', package:'@devgraph/web', version:packageVersion, abi:'devgraph.web.v1',
  source_commit:capture('git',['rev-parse','HEAD']),
  source_dirty:capture('git',['status','--porcelain']).length > 0,
  wasm_runtime_packages:metadata.packages.filter(p=>runtimeIds.has(p.id)).map(({name,version,source})=>({name,version,source})),
  protocol_source:{repository:'https://github.com/ZenithResearch/devgraph',path:'crates/devgraph-work-protocol',revision:capture('git',['rev-parse','HEAD'])},
  source_dependencies:gitPackages.map(({name,version,source})=>({name,version,source})).sort((a,b)=>a.name.localeCompare(b.name)),
  resolved_features:metadata.resolve.nodes.filter(n=>gitPackages.some(p=>p.id===n.id)).map(({id,features})=>({id,features})),
  toolchains:{rust:capture('rustc',['--version']),wasm_bindgen:'0.2.127',node:process.version,typescript:'5.9.3',vite:'7.3.5',playwright:'1.61.1'},
  target:'wasm32-unknown-unknown',wasm_sha256:digest(wasm),runtime_js_sha256:runtimeDigest,cargo_lock_sha256:digest(lock),
  ...evidenceInputs,
  credential_transport_schema:'devgraph.credential-transport-capabilities.v2',
  request_schemas:['devgraph.work-request.v1','devgraph.work-request.v2','devgraph.arena-request.v1'],
  receiver_profile_schema:'devgraph.native-profile.v2',
  compatibility_bridge_schema:'devgraph.bridge.v1',
  runtime_files:runtimeFiles.map(file=>({file,sha256:digest(resolve(dist,file))})),
  qualification:{core_browsers:browserEvidence?'passed':'pending',chrome_macos_provider:'pending',installed_macos_vm:'blocked',legacy_retirement:'gated'},
  browser_evidence:browserEvidence,
};
writeFileSync(resolve(here,'sdk-build.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(`Built ${wasm} (${readFileSync(wasm).length} bytes); pack explicitly with npm pack --ignore-scripts.`);
