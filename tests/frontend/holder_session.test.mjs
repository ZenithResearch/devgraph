import assert from 'node:assert/strict';
import test from 'node:test';
import {webcrypto} from 'node:crypto';
import {createBrowserHolderSession,canonicalSessionJson} from '../../src/devgraph/frontend/static/credential-v2/holder-session.mjs';

const hex=b=>Buffer.from(b).toString('hex');
const canonical=canonicalSessionJson;
const domain=(name,v)=>new TextEncoder().encode(name+'\0'+canonical(v));
async function fixture(){
 let now=1000;const issuer=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']);
 const publicKey=hex(await webcrypto.subtle.exportKey('raw',issuer.publicKey));
 const origin='https://graph.example',service='service:graph',holder='a'.repeat(64),installation='b'.repeat(32);
 const profile={presentations:[{issuer:'synthetic',key_id:'key',public_key:publicKey,audience:'devgraph',callers:[{kind:'browser',id:origin}]}]};
 const session=await createBrowserHolderSession({origin,service,holderPublicKey:holder,installationId:installation,profile,ttlSeconds:60,cryptoImpl:webcrypto,now:()=>now});
 const challenge={schema:'secs.holder-session-challenge.v1',challenge_id:'c'.repeat(32),request_id:'d'.repeat(32),nonce:'e'.repeat(64),issuer:'synthetic',key_id:'key',service,audience:'devgraph',origin,parent_grant_id:'f'.repeat(32),parent_grant_digest:'1'.repeat(64),parent_revision:'revision-one',holder_public_key:holder,installation_id:installation,session_installation_id:session.installationId,session_public_key:session.publicKey,permissions:[{operation:'devgraph.work.list',fixture:'exact signed scope'}],issued_at:now,review_deadline:now+30,expires_at:now+60};
 const issue=async(value=challenge)=>{const claims={schema:'secs.holder-session-credential.v1',credential_id:'2'.repeat(32),challenge_digest:hex(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)))),challenge:structuredClone(value)};return{claims,signature:hex(await webcrypto.subtle.sign('Ed25519',issuer.privateKey,domain('secs.holder-session-credential.v1/signature',claims)))};};
 return{session,challenge,issue,profile,now:value=>{now=value;}};
}

test('temporary key is nonextractable; finite client proofs work without another Wallet call',async()=>{
 let privateKey;
 const cryptoImpl={getRandomValues:b=>webcrypto.getRandomValues(b),subtle:{generateKey:async(...args)=>{const pair=await webcrypto.subtle.generateKey(...args);privateKey=pair.privateKey;return pair;},exportKey:(...args)=>webcrypto.subtle.exportKey(...args)}};
 // Observe the actual generateKey policy without exposing a key through the public API.
 const f=await fixture();
 const session=await createBrowserHolderSession({origin:'https://graph.example',service:'service:graph',holderPublicKey:'a'.repeat(64),installationId:'b'.repeat(32),profile:f.profile,ttlSeconds:60,cryptoImpl,now:()=>1000});
 assert.equal(privateKey.extractable,false);await assert.rejects(webcrypto.subtle.exportKey('pkcs8',privateKey));session.dispose();
 const possession=await f.session.prepare(f.challenge);const key=await webcrypto.subtle.importKey('raw',Buffer.from(f.session.publicKey,'hex'),'Ed25519',false,['verify']);
 assert.equal(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(possession,'hex'),domain('secs.holder-session-possession.v1/signature',f.challenge)),true);
 await f.session.accept(await f.issue());
 const call={operation:'devgraph.work.list',resource:'https://work.zenith-research.ca/devgraph',parameters:{path:'/todos/v2?limit=1'}};
 const a=await f.session.present(call),b=await f.session.present(call);assert.notEqual(a.proof.nonce,b.proof.nonce);
 for(const envelope of [a,b]){const proof={...envelope.proof};delete proof.signature;assert.equal(await webcrypto.subtle.verify('Ed25519',key,Buffer.from(envelope.proof.signature,'hex'),domain('secs.holder-session-presentation.v1/signature',proof)),true);assert.equal(proof.expires_at,1060);assert.equal('parent_grant' in envelope.credential,false);}
 f.now(1059);await f.session.present(call);assert.equal(f.session.expiresAt,1060);
 f.now(1060);await assert.rejects(f.session.present(call),/holder_session_expired/);assert.equal(f.session.active,false);
});

for(const field of ['origin','service','holder_public_key','installation_id','session_installation_id','session_public_key','audience','issuer','key_id','expires_at','review_deadline','unknown']){
 test('session challenge rejects changed '+field,async()=>{
  const f=await fixture(),value=structuredClone(f.challenge);value[field]=field==='expires_at'?1061:field==='review_deadline'?1061:'substituted';
  await assert.rejects(f.session.prepare(value),/invalid_holder_session/);assert.equal(f.session.active,false);
 });
}
test('issuer signature, reviewed scope and exact challenge cannot be substituted',async()=>{
 for(const attack of ['signature','scope','challenge_digest','unknown']){
  const f=await fixture();await f.session.prepare(f.challenge);let credential=await f.issue();
  if(attack==='signature')credential.signature='0'.repeat(128);
  if(attack==='scope')credential=await f.issue({...f.challenge,permissions:[{operation:'different'}]});
  if(attack==='challenge_digest')credential.claims.challenge_digest='0'.repeat(64);
  if(attack==='unknown')credential.extra=true;
  await assert.rejects(f.session.accept(credential),/invalid_holder_session/);assert.equal(f.session.active,false);
 }
});
test('prepared review is immutable, installation is one-shot and dispose closes all signing',async()=>{
 const f=await fixture();await f.session.prepare(f.challenge);const credential=await f.issue();f.challenge.permissions[0].operation='changed';await f.session.accept(credential);
 await assert.rejects(f.session.accept(credential),/holder_session_state/);
 f.session.dispose();await assert.rejects(f.session.present({}),/holder_session_closed/);await assert.rejects(f.session.prepare(f.challenge),/holder_session_closed/);
});
test('clock rollback and an expired review cannot extend the session',async()=>{
 const f=await fixture();await f.session.prepare(f.challenge);await f.session.accept(await f.issue());f.now(999);await assert.rejects(f.session.present({}),/holder_session_clock_changed/);assert.equal(f.session.active,false);
 const old=await fixture();old.now(1030);await assert.rejects(old.session.prepare(old.challenge),/invalid_holder_session/);
});
test('canonical session bytes reject unsafe values and sort Unicode keys as RFC 8785',()=>{
 assert.equal(canonical({'\uE000':1,'😀':2}),'{"😀":2,"":1}');
 for(const value of [1.5,Number.MAX_SAFE_INTEGER+1,NaN,undefined,{'bad':undefined},{bad:'\ud800'},Object.create({a:1})])assert.throws(()=>canonical(value),/invalid_holder_session/);
});
