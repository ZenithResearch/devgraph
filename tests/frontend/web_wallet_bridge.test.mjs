import {test} from 'node:test';import assert from 'node:assert/strict';
import {WebWalletProvider} from '../../src/devgraph/frontend/static/credential-v2/web-wallet.mjs';
function fixture(){let receive;const messages=[],timers=new Set();const popup={closed:false,postMessage:(m,origin)=>messages.push({m,origin}),close(){this.closed=true;}};const window={crypto:{randomUUID:()=> 'nonce'},addEventListener:(_,f)=>receive=f,removeEventListener(){},setInterval:f=>{timers.add(f);return f;},clearInterval:f=>timers.delete(f),setTimeout:()=>1,clearTimeout(){}};let locked=0;const provider=new WebWalletProvider({window,popup,url:'https://wallet.example/wallet-connect',onDisconnect:()=>locked++});const send=(m,origin='https://wallet.example',source=popup)=>receive({data:{channel:'castalia.wallet.window.v1',nonce:'nonce',...m},origin,source});return{provider,popup,messages,send,locked:()=>locked};}
test('Wallet responses require exact window, origin, nonce and request id',async()=>{const f=fixture();f.send({type:'ready'});await f.provider.ready();const result=f.provider.getCapabilities();f.send({type:'response',id:1,ok:true,value:['bad']},'https://evil.example');f.send({type:'response',id:1,ok:true,value:['bad'],nonce:'wrong'});f.send({type:'response',id:2,ok:true,value:['bad']});f.send({type:'response',id:1,ok:true,value:['read_access_v1']});assert.deepEqual(await result,['read_access_v1']);assert.equal(f.messages[0].origin,'https://wallet.example');f.provider.dispose();});
test('closing Wallet rejects pending calls and notifies monitor',async()=>{const f=fixture();const result=f.provider.requestConnection();f.provider.dispose();await assert.rejects(result,/wallet_connection_changed/);assert.equal(f.locked(),1);});

test('embedded Wallet uses a separate channel and never closes its parent window',async()=>{
 let receive,closed=0,removed=0;const messages=[];
 const peer={closed:false,postMessage:(m,origin)=>messages.push({m,origin}),close:()=>closed++};
 const window={location:{origin:'https://graph.example'},crypto:{randomUUID:()=> 'embed-nonce'},addEventListener:(_,f)=>receive=f,removeEventListener(){},setInterval:()=>1,clearInterval(){},setTimeout:()=>2,clearTimeout(){}};
 const p=new WebWalletProvider({window,popup:peer,url:'https://wallet.example/wallet-connect',mode:'embed',onDispose:()=>removed++});
 const send=(channel,extra)=>receive({source:peer,origin:'https://wallet.example',data:{channel,nonce:'embed-nonce',...extra}});
 send('castalia.wallet.embed.v1',{type:'ready'});await p.ready();
 const result=p.getCapabilities();
 assert.equal(messages.at(-1).m.channel,'castalia.wallet.embed.v1');
 send('castalia.wallet.window.v1',{type:'response',id:1,ok:true,value:['bad']});
 send('castalia.wallet.embed.v1',{type:'response',id:1,ok:true,value:['read_access_v1']});
 assert.deepEqual(await result,['read_access_v1']);p.dispose();assert.equal(closed,0);assert.equal(removed,1);
});
test('embedding refuses same-origin custody and insecure Wallet URLs',()=>{
 const window={location:{origin:'https://graph.example'}};
 for(const url of ['https://graph.example/wallet-connect','http://wallet.example','https://user:pass@wallet.example'])assert.throws(()=>new WebWalletProvider({window,popup:{},url,mode:'embed'}),/wallet_embed_invalid/);
});
