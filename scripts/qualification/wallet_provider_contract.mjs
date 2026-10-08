// Source interoperability only. No extension install, keys, grants or live calls.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {prepareProviderSetup} from '../../src/devgraph/frontend/static/credential-v2/provider-setup.mjs';
const root=resolve(fileURLToPath(new URL('../../',import.meta.url)));
if(!process.argv[2])throw Error('Pass a Wallet Git repository containing the pinned merged revision');
const repo=resolve(process.argv[2]);
const sources=JSON.parse(await readFile(join(root,'docs/dev/generic-wallet-source-set.json'),'utf8'));
const revision=sources.sources.wallet_generic.revision;
assert.match(revision,/^[0-9a-f]{40}$/);
const source=execFileSync('git',['-C',repo,'show',`${revision}:packages/provider-types/src/provider-profiles.ts`]);
const scratch=await mkdtemp(join(tmpdir(),'devgraph-wallet-profile-contract-'));
try{
  const modulePath=join(scratch,'provider-profiles.mts');await writeFile(modulePath,source);
  // Node 24+ strips the type-only import; the actual pinned parser is unmodified.
  const {parseProviderProfile}=await import(pathToFileURL(modulePath));
  const raw=await readFile(join(root,'tests/fixtures/credential-v2/wallet-provider-profile.json'));
  const profile=JSON.parse(raw),calls=[];
  assert.deepEqual(parseProviderProfile(profile),profile);
  const setup=await prepareProviderSetup({origin:profile.origins[0],
    transport:{getProviderProfile:async()=>profile},
    provider:{getCapabilities:async()=>['connection_v1','provider_profiles_v1','credential_presentation_v2'],
      requestConnection:()=>{calls.push('connect');return Promise.resolve({state:'connected'});},
      proposeProviderProfile:value=>{calls.push(parseProviderProfile(value));return Promise.resolve({state:'approved'});}}});
  assert.deepEqual(calls,[]);const approval=setup.approve();assert.deepEqual(calls,[profile]);await approval;
  const connected=setup.connect();assert.equal(calls.at(-1),'connect');await connected;
  for(const change of [p=>p.presentations[0].callers[0].kind='terminal',p=>p.secret='unexpected',p=>p.presentations.push(p.presentations[0])]){
    const invalid=structuredClone(profile);change(invalid);assert.throws(()=>parseProviderProfile(invalid));
  }
  const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
  const report={schema:'devgraph.wallet-provider-source-check.v1',status:'passed',
    wallet_revision:revision,wallet_parser_sha256:hash(source),fixture_sha256:hash(raw),node:process.version,
    devgraph_setup_sha256:hash(await readFile(join(root,'src/devgraph/frontend/static/credential-v2/provider-setup.mjs'))),
    installed_qualification:'not_run',cases:['exact_profile','separate_synchronous_ceremonies','invalid_profile_rejection']};
  await mkdir(join(root,'.sdk-validation'),{recursive:true});
  await writeFile(join(root,'.sdk-validation/wallet-provider-contract.json'),JSON.stringify(report,null,2)+'\n');
  console.log('Merged Wallet provider contract passed (source only).');
}finally{await rm(scratch,{recursive:true,force:true});}
