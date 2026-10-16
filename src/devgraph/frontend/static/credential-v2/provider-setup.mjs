/** Public configuration is a proposal, never authority or automatic trust. */
const failure=code=>Object.assign(new Error(code),{code,dispatched:false});
const shape=(value,keys)=>value&&Object.getPrototypeOf(value)===Object.prototype
  && Object.keys(value).sort().join(',')===[...keys].sort().join(',');
function validate(profile,origin){
  if(!shape(profile,['schema','display_name','origins','membership','presentations'])
    ||profile.schema!=='castalia.provider-profile.v1'||profile.membership!==null
    ||typeof profile.display_name!=='string'||!/^[ -~]{1,160}$/.test(profile.display_name)
    ||!Array.isArray(profile.origins)||profile.origins.length!==1||profile.origins[0]!==origin
    ||!Array.isArray(profile.presentations)||!profile.presentations.length||profile.presentations.length>64)
    throw failure('wallet_provider_setup_invalid');
  const seen=new Set();
  for(const pin of profile.presentations){
    if(!shape(pin,['issuer','key_id','public_key','audience','callers'])
      ||['issuer','key_id','audience'].some(k=>typeof pin[k]!=='string'||!/^[ -~]{1,200}$/.test(pin[k]))
      ||typeof pin.public_key!=='string'||!/^[a-f0-9]{64}$/.test(pin.public_key)
      ||!Array.isArray(pin.callers)||pin.callers.length!==1
      ||!shape(pin.callers[0],['kind','id'])||pin.callers[0].kind!=='browser'||pin.callers[0].id!==origin)
      throw failure('wallet_provider_setup_invalid');
    const id=JSON.stringify([pin.issuer,pin.key_id,pin.audience]);
    if(seen.has(id))throw failure('wallet_provider_setup_invalid');seen.add(id);
  }
  if(new TextEncoder().encode(JSON.stringify(profile)).length>65536)throw failure('wallet_provider_setup_invalid');
}
/** Call before enabling controls. No connection, trust or signing consent occurs here. */
export async function prepareProviderSetup({provider,transport,origin,current=()=>{}}){
  current();
  if(typeof provider?.getCapabilities!=='function'||typeof provider?.requestConnection!=='function'
    ||typeof provider?.proposeProviderProfile!=='function')throw failure('wallet_upgrade_required');
  const capabilities=await provider.getCapabilities();current();
  if(!Array.isArray(capabilities)||!['credential_presentation_v2','provider_profiles_v1','connection_v1']
    .every(c=>capabilities.includes(c)))throw failure('wallet_upgrade_required');
  const profile=await transport.getProviderProfile();current();validate(profile,origin);
  // Keep the approved proposal separate from caller-owned display objects.
  const snapshot=structuredClone(profile);
  return Object.freeze({
    get profile(){return structuredClone(snapshot);},
    approve(){
      current();
      return provider.proposeProviderProfile(structuredClone(snapshot)).then(result=>{
        current();if(result?.state!=='approved')throw failure('user_denied');return result;
      });
    },
    connect(){
      current();
      return provider.requestConnection().then(result=>{
        current();if(result?.state!=='connected')throw failure('user_denied');return result;
      });
    },
  });
}
