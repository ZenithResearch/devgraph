// Public route authentication only. Wallet Core repeats verification against
// its approved issuer before signing. TLS and site consent remain independent.
const keys=(v,names)=>v&&Object.getPrototypeOf(v)===Object.prototype&&Object.keys(v).sort().join(',')===[...names].sort().join(',');
const stable=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(stable).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';
export async function verifyServiceRoute(value,{origin,profile,service,now=Math.floor(Date.now()/1000)}){
 const fail=()=>{throw Error('invalid_service_binding');};
 if(!keys(value,['schema','binding'])||value.schema!=='devgraph.service-access-point.v1'||!keys(value.binding,['claims','signature']))fail();
 const {claims:c,signature}=value.binding;
 if(!keys(c,['schema','issuer','key_id','service','origin','authority_receiver','prefix','not_before','not_after'])||c.schema!=='secs.service-access-point.v1'||c.origin!==origin||c.service!==service||c.prefix!=='/service/v1'||!Number.isSafeInteger(c.not_before)||!Number.isSafeInteger(c.not_after)||c.not_before<0||c.not_before>now||now>=c.not_after||c.not_after-c.not_before>604800||!/^[a-f0-9]{128}$/.test(signature))fail();
 for(const address of [c.origin,c.authority_receiver]){const u=new URL(address);if(u.protocol!=='https:'||u.origin!==address)fail();}
 const pin=profile?.presentations?.find(p=>p.issuer===c.issuer&&p.key_id===c.key_id&&p.audience==='devgraph'&&p.callers?.some(p=>p.kind==='browser'&&p.id===origin));
 if(!pin||!/^[a-f0-9]{64}$/.test(pin.public_key))fail();
 const bytes=s=>Uint8Array.from(s.match(/../g),b=>parseInt(b,16));
 const key=await crypto.subtle.importKey('raw',bytes(pin.public_key),{name:'Ed25519'},false,['verify']);
 if(!await crypto.subtle.verify('Ed25519',key,bytes(signature),new TextEncoder().encode('secs.service-access-point.v1\0'+stable(c))))fail();
 return structuredClone(value.binding);
}
