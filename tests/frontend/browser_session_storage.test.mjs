import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {loginReturnPath} from '../../src/devgraph/frontend/static/credential-v2/session-store.mjs';
import {createBrowserLoginSession,restoreBrowserHolderSession,canonicalSessionJson} from '../../src/devgraph/frontend/static/credential-v2/holder-session.mjs';

const hex=value=>Buffer.from(value).toString('hex');
async function fixture(){
 let now=1000;
 const issuer=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const origin='https://graph.example',service='service:graph';
 const profile={presentations:[{issuer:'fixture',key_id:'key',public_key:hex(await webcrypto.subtle.exportKey('raw',issuer.publicKey)),audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};
 const options={origin,service,profile,ttlSeconds:300,cryptoImpl:webcrypto,now:()=>now};
 const session=await createBrowserLoginSession(options);
 const challenge={schema:'secs.holder-session-challenge.v1',challenge_id:'c'.repeat(32),request_id:'d'.repeat(32),nonce:'e'.repeat(64),issuer:'fixture',key_id:'key',service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'2'.repeat(64),holder_public_key:'a'.repeat(64),installation_id:'b'.repeat(32),session_installation_id:session.installationId,session_public_key:session.publicKey,permissions:[{operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph'}],issued_at:1000,review_deadline:1120,expires_at:1300};
 const issue=async()=>{const claims={schema:'secs.holder-session-credential.v1',credential_id:'3'.repeat(32),challenge_digest:hex(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalSessionJson(challenge)))),challenge};return{claims,signature:hex(await webcrypto.subtle.sign('Ed25519',issuer.privateKey,new TextEncoder().encode('secs.holder-session-credential.v1/signature\0'+canonicalSessionJson(claims))))};};
 return{session,options,challenge,issue,now:value=>{now=value;}};
}

test('popup login prepares child possession before the browser knows the Wallet identity',async()=>{
 const f=await fixture();assert.match(await f.session.prepare(f.challenge),/^[a-f0-9]{128}$/);
 const saved=f.session.checkpoint();assert.equal(saved.privateKey.extractable,false);
 assert.equal(saved.holderPublicKey,f.challenge.holder_public_key);
 assert.equal(saved.installationId,f.challenge.installation_id);
 assert.equal(saved.credential,null);assert.equal('parent_grant' in saved,false);
 f.session.dispose();
});

test('reload restores the verified original credential after review expiry without extending its end',async()=>{
 const f=await fixture();await f.session.prepare(f.challenge);await f.session.accept(await f.issue());
 const record=structuredClone(f.session.checkpoint());f.session.dispose();f.now(1130);
 const restored=await restoreBrowserHolderSession(record,f.options);
 assert.equal(restored.active,true);assert.equal(restored.publicKey,record.publicKey);
 assert.equal(restored.expiresAt,1300);assert.equal(restored.checkpoint().privateKey.extractable,false);
 const a=await restored.status(),b=await restored.status();assert.notEqual(a.nonce,b.nonce);
 assert.equal(a.request_id,f.challenge.request_id);assert.equal(a.expires_at,1190);
 f.now(1300);await assert.rejects(restored.status(),/holder_session_expired/);
});

test('an unissued checkpoint restores only original-key recovery, never active read authority',async()=>{
 const f=await fixture();await f.session.prepare(f.challenge);const record=structuredClone(f.session.checkpoint());
 f.session.dispose();f.now(1130);const restored=await restoreBrowserHolderSession(record,f.options);
 assert.equal(restored.active,false);assert.equal((await restored.status()).request_id,f.challenge.request_id);
 await assert.rejects(restored.present({}),/holder_session_state/);
});

for(const attack of ['origin','service','profile','publicKey','extractable','clock','expired','extra'])test('stored child session rejects '+attack+' before resuming',async()=>{
 const f=await fixture();await f.session.prepare(f.challenge);await f.session.accept(await f.issue());
 const record=structuredClone(f.session.checkpoint());f.session.dispose();
 if(attack==='origin')record.origin='https://other.example';
 if(attack==='service')record.service='other-service';
 if(attack==='profile')record.profile.presentations[0].public_key='0'.repeat(64);
 if(attack==='publicKey')record.publicKey='4'.repeat(64);
 if(attack==='extractable')record.privateKey=(await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify'])).privateKey;
 if(attack==='clock')f.now(999);
 if(attack==='expired')f.now(1300);
 if(attack==='extra')record.parent_grant={};
 await assert.rejects(restoreBrowserHolderSession(record,f.options));
});

for(const path of ['/monitor','/monitor#topology','/monitor/kanban/'])test('login return accepts '+path,()=>assert.equal(loginReturnPath(path),path));
for(const path of ['https://evil.example/monitor','//evil.example/monitor','/work-operations/v2','/monitor?token=secret','/login','/monitor#bad value','/monitor\\evil','/../monitor#topology','monitor#topology','/monitor#topology\n'])test('login return rejects '+JSON.stringify(path),()=>assert.equal(loginReturnPath(path),'/monitor'));
