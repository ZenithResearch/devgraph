// Ephemeral synthetic secS producer + the shipped page producer. Never live authority.
import { webcrypto, generateKeyPairSync, sign } from 'node:crypto';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const c=vm.createContext({crypto:webcrypto,TextEncoder,Uint8Array,structuredClone,btoa,location:{origin:'http://127.0.0.1:8080'}});
vm.runInContext(readFileSync(new URL('../../../src/devgraph/frontend/static/topology/proof.js',import.meta.url),'utf8'),c);
const page=await c.DevgraphMonitorProof.createPage();
const {privateKey,publicKey}=generateKeyPairSync('ed25519'),now=Math.floor(Date.now()/1000);
const session={actor_id:'pubkey:sha256:'+'42'.repeat(32),actor_signature_suite:'Ed25519',audience:'devgraph://receiver-local',expires_at:now+300,issued_at:now,nonce:'AAECAwQFBgcICQoL',operation:'devgraph.monitor.view.read.v2',origin:c.location.origin,page_public_key_base64url:page.publicKey,receiver_policy_digest_sha256:'12'.repeat(32),receiver_policy_id:'topology-test',receiver_policy_version:1,schema:'secs-devgraph-monitor-session.v2',schema_version:2,secs_context_id:'ctx:sha256:'+'24'.repeat(32),secs_verifier_key_id:'test-topology',secs_verifier_signature_suite:'Ed25519',session_id:'AAECAwQFBgcICQoLDA0ODw',wallet_presentation_digest_sha256:'19'.repeat(32)};
const canonical=value=>JSON.stringify(value,Object.keys(value).sort());
session.secs_verifier_signature=sign(null,Buffer.from('secs-devgraph-monitor-session.v2/signature\0'+canonical(session)),privateKey).toString('base64url');
await page.install(session);
const target='/monitor/topology/v1?work_kind=Task';
console.log(JSON.stringify({now,target,publicKey:publicKey.export({format:'jwk'}).x,headers:await page.headers(target)}));
