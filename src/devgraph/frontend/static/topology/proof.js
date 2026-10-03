/* Ephemeral page-key producer for the explicitly provisioned signed v2 profile. */
(function(root){
 'use strict';
 const enc=new TextEncoder();
 const b64=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
 const canonical=value=>JSON.stringify(value,Object.keys(value).sort());
 const concat=(domain,value)=>enc.encode(domain+'\0'+canonical(value));
 const digest=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
 async function createPage(){
   const keys=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']);
   const publicKey=b64(await crypto.subtle.exportKey('raw',keys.publicKey));
   let session=null,sessionHeader=null,sessionDigest=null;
   return {publicKey,
     async install(s){
       if(s.operation!=='devgraph.monitor.view.read.v2'||s.schema!=='secs-devgraph-monitor-session.v2'||s.schema_version!==2||s.page_public_key_base64url!==publicKey||s.origin!==location.origin)throw new Error('Session does not match this page');
       session=structuredClone(s);sessionHeader=b64(enc.encode(canonical(session)));
       sessionDigest=await digest(concat('secs-devgraph-monitor-session.v2/session',session));
     },
     clear(){session=null;sessionHeader=null;sessionDigest=null;},
     async headers(target){
       if(!session||Date.now()/1000>=session.expires_at)throw new Error('Reconnect signed access');
       if(!target.startsWith('/monitor/topology/v1')||target.split('?')[0]!=='/monitor/topology/v1')throw new Error('Unsupported signed read');
       const proof={body_digest_sha256:await digest(new Uint8Array()),method:'GET',nonce:b64(crypto.getRandomValues(new Uint8Array(12))),operation:'devgraph.monitor.view.read.v2',origin:location.origin,path_query:target,schema:'devgraph-monitor-request-proof.v2',schema_version:2,session_digest_sha256:sessionDigest,session_id:session.session_id,signature_suite:'Ed25519',timestamp:Math.floor(Date.now()/1000)};
       proof.signature=b64(await crypto.subtle.sign('Ed25519',keys.privateKey,concat('devgraph.monitor.view.read.v2/request-proof',proof)));
       return {'SecS-Devgraph-Monitor-Origin':location.origin,'SecS-Devgraph-Monitor-Session':sessionHeader,'SecS-Devgraph-Monitor-Proof':b64(enc.encode(canonical(proof)))};
     }
   };
 }
 root.DevgraphMonitorProof={createPage};
})(globalThis);
