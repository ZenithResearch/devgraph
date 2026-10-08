/** Devgraph-owned browser transport. Reads do not depend on Wallet authority. */
import {DevgraphCredentialClient} from '../../../src/devgraph/frontend/static/credential-v2/coordinator.mjs';
import {createDevgraphHttpTransport} from '../../../src/devgraph/frontend/static/credential-v2/transport.mjs';
import {prepareProviderSetup} from '../../../src/devgraph/frontend/static/credential-v2/provider-setup.mjs';
import {subjectPublicKey} from '../../../src/devgraph/frontend/static/credential-v2/subject.mjs';
/** @param {string} code @param {boolean} [dispatched] */
const failure = (code, dispatched = false) => Object.assign(new Error(code), {code, dispatched});
/** @typedef {{origin?:string,readCredential?:string,provider?:any,fetch?:typeof fetch}} GenericOptions */
/** @typedef {{kind:'committed'} & (import('./domain.js').MutationResult | import('./domain.js').ProgressMutationResult) | {kind:'not_dispatched'|'rejected'|'outcome_unknown',error:Error & {code:string,dispatched:boolean}}} GenericMutationOutcome */
export class GenericClient {
  /** @type {any} */ #wasm;
  /** @type {any} */ #provider;
  #origin; #credential; #fetch; #transport;
  #controller = new AbortController();
  /** @type {DevgraphCredentialClient|null} */ #signer = null;
  /** @type {{raw:string,key:string}|null} */ #pending = null;
  #busy = false; #disposed = false;
  /** @type {Set<()=>void>} */ #releases = new Set();
  /** @param {any} wasm @param {GenericOptions} options */
  constructor(wasm, options = {}) {
    const origin = options.origin ?? globalThis.location?.origin;
    if (!origin) throw failure('invalid_origin');
    const url = new URL(origin);
    if (url.origin !== origin || (globalThis.location && globalThis.location.origin !== origin)
      || !['https:', 'http:'].includes(url.protocol)
      || url.protocol === 'http:' && !['127.0.0.1','localhost','[::1]'].includes(url.hostname)) throw failure('invalid_origin');
    const credential = options.readCredential ?? '';
    if (typeof credential !== 'string' || credential.length > 8192 || /[\r\n]/.test(credential)) throw failure('invalid_read_credential');
    this.#wasm=wasm; this.#origin=origin; this.#credential=credential;
    this.#fetch=options.fetch ?? globalThis.fetch;
    this.#provider=options.provider ?? /** @type {any} */(globalThis).castaliaWallet;
    this.#transport=createDevgraphHttpTransport({origin,fetch:this.#fetch});
    globalThis.addEventListener?.('pagehide',this.#leaving,{once:true});
  }
  /** Load public provider setup before enabling separate approval and connection buttons. */
  async prepareWalletSetup() {
    this.#current();
    const setup=await prepareProviderSetup({provider:this.#provider,transport:this.#transport,
      origin:this.#origin,current:()=>this.#current()});
    return Object.freeze({get profile(){return setup.profile;},approve:()=>setup.approve(),
      connect:()=>{this.#current();if(this.#busy)throw failure('operation_in_progress');const pending=setup.connect();this.#signer?.dispose();this.#signer=null;return pending;}});
  }
  #leaving = () => this.dispose();
  #current() {if(this.#disposed) throw failure('disposed');}
  /** @param {AbortSignal|undefined} signal */
  #signal(signal) {return signal ? AbortSignal.any([signal,this.#controller.signal]) : this.#controller.signal;}
  /** @param {any} input @param {{signal?:AbortSignal}} [options] */
  async read(input, options = {}) {
    this.#current(); if(!this.#credential) throw failure('read_credential_required');
    const descriptor=this.#wasm.read_descriptor(input);
    if (typeof descriptor.path !== 'string' || !descriptor.path.startsWith('/') || descriptor.path.startsWith('//')) throw failure('invalid_read');
    const response=await this.#fetch(this.#origin+descriptor.path,{
      method:descriptor.method,mode:'same-origin',credentials:'omit',redirect:'error',cache:'no-store',
      headers:{Authorization:`Bearer ${this.#credential}`,Accept:'application/json',...(descriptor.body ? {'Content-Type':'application/json'} : {})},
      body:descriptor.body ?? undefined,signal:this.#signal(options.signal),
    });
    if(response.url && new URL(response.url).origin!==this.#origin) throw failure('invalid_origin');
    const maximum=response.status>=400 ? Math.min(65536,descriptor.max_response_bytes) : descriptor.max_response_bytes;
    const reader=response.body?.getReader(); if(!reader)throw failure('invalid_response');
    let size=0; /** @type {Uint8Array[]} */ const chunks=[];
    try {for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>maximum)throw failure('resource_limit');chunks.push(part.value);}}
    catch(error){await reader.cancel();throw error;} finally {reader.releaseLock();}
    this.#current(); if(options.signal?.aborted)throw failure('cancelled');
    const raw=new Uint8Array(size);let offset=0;for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.length;}
    return this.#wasm.decode_read(raw,{status:response.status,content_type:response.headers.get('content-type'),content_encoding:response.headers.get('content-encoding'),max_bytes:maximum},descriptor.request);
  }
  /** @param {import('./domain.js').WorkKind} kind @param {string} id @param {{signal?:AbortSignal}} [options] @returns {Promise<import('./domain.js').ProgressWork>} */
  getTodo(kind,id,options={}) {return this.read({kind:'get_todo',work_kind:kind,id},options);}
  /** @param {import('./domain.js').WorkKind} kind @param {string} id @param {{signal?:AbortSignal}} [options] @returns {Promise<import('./domain.js').Work>} */
  getWork(kind,id,options={}) {return this.read({kind:'get_work',work_kind:kind,id},options);}
  /** @param {import('./index.js').WorkFilter} filter @param {{signal?:AbortSignal}} [options] @returns {AsyncGenerator<import('./domain.js').Work>} */
  async *iterateWork(filter,options={}) {
    const stable=structuredClone(filter),limit=stable.limit??50;let after=stable.after_id;
    for(;;){const page=await this.read({kind:'list_work',work_kind:stable.kind,filters:{include_archived:stable.include_archived??false,descending:stable.descending??false},limit,...(after===undefined?{}:{after_id:after})},options);
      if(!Array.isArray(page.items)||page.items.length>limit)throw failure('invalid_page');
      for(const item of page.items){if(after!==undefined&&(stable.descending?item.id>=after:item.id<=after))throw failure('non_advancing_page');after=item.id;yield item;}
      if(page.items.length<limit)return;
    }
  }
  /** @param {import('./domain.js').Subject} subject @param {import('./index.js').Relationship} relation_kind @param {{limit?:number,after_resource?:string,signal?:AbortSignal}} [options] @returns {AsyncGenerator<import('./domain.js').Work>} */
  async *iterateRelations(subject,relation_kind,options={}) {
    const stable=structuredClone(subject),limit=options.limit??50;let after=options.after_resource;
    for(;;){const page=await this.read({kind:'list_relations',subject:stable,relation_kind,limit,...(after===undefined?{}:{after_resource:after})},options);
      if(!Array.isArray(page.items)||page.items.length>limit)throw failure('invalid_page');
      for(const item of page.items){const key=`${item.kind}/${item.id}`;if(after!==undefined&&key<=after)throw failure('non_advancing_page');after=key;yield item;}
      if(page.items.length<limit)return;
    }
  }
  /** @param {Uint8Array} request @param {import('./domain.js').ExpectedResult} expected_result @param {{signal?:AbortSignal}} [options] @returns {Promise<import('./domain.js').CypherResult>} */
  cypher(request,expected_result,options={}) {
    if(!(request instanceof Uint8Array)||request.length>32768)throw failure('invalid_read');
    return this.read({kind:'cypher',request_b64:btoa(String.fromCharCode(...request)),expected_result:structuredClone(expected_result)},options);
  }
  /** @param {import('./domain.js').WorkRequestInput} request @param {{idempotency_key:string}} options */
  prepare(request,options){this.#current();return this.#prepared(this.#wasm.prepare_value(request,options.idempotency_key),options.idempotency_key);}
  /** @param {Uint8Array} raw @param {{idempotency_key:string}} options */
  prepareBytes(raw,options){this.#current();return this.#prepared(this.#wasm.prepare_bytes(raw,options.idempotency_key),options.idempotency_key);}
  /** @param {any} prepared @param {string} key */
  #prepared(prepared,key) {
    const canonical=/** @type {Uint8Array} */(prepared.canonical_bytes().slice()), raw=new TextDecoder('utf-8',{fatal:true}).decode(canonical);
    const summary=/** @type {import('./index.js').PreparedSummary} */(structuredClone(prepared.summary()));let disposed=false;const controller=new AbortController();
    const release=()=>{if(disposed)return;disposed=true;controller.abort();prepared.free();this.#releases.delete(release);};
    this.#releases.add(release);
    return Object.freeze({
      get canonical_bytes(){return canonical.slice();}, get summary(){return structuredClone(summary);},
      execute: async (/** @type {{signal?:AbortSignal}} */ options={})=>{if(disposed)throw failure('disposed');return this.#execute(prepared,raw,key,false,options.signal?AbortSignal.any([options.signal,controller.signal]):controller.signal);},
      reconcile: async (/** @type {{signal?:AbortSignal}} */ options={})=>{if(disposed)throw failure('disposed');return this.#execute(prepared,raw,key,true,options.signal?AbortSignal.any([options.signal,controller.signal]):controller.signal);},
      dispose: release,
    });
  }
  /** @param {any} prepared @param {string} raw @param {string} key @param {boolean} reconcile @param {AbortSignal|undefined} signal @returns {Promise<GenericMutationOutcome>} */
  async #execute(prepared,raw,key,reconcile,signal) {
    this.#current(); if(this.#busy)throw failure('operation_in_progress');
    if(this.#pending && (!reconcile||this.#pending.raw!==raw||this.#pending.key!==key))throw failure('reconciliation_required',true);
    this.#busy=true;let received=false;
    try {
      const supported=await this.#transport.getCapabilities();this.#current();
      const summary=prepared.summary();
      if(!supported.operations?.includes(summary.operation))throw failure('unsupported_operation');
      if(!this.#signer){
        if(typeof this.#provider?.getCapabilities!=='function' || typeof this.#provider?.presentCredential!=='function'
          || !(await this.#provider.getCapabilities()).includes('credential_presentation_v2'))throw failure('capability_unavailable');
        this.#current();
        const holderPublicKey=subjectPublicKey(await this.#provider?.getSubject());this.#current();
        this.#signer=new DevgraphCredentialClient({provider:this.#provider,transport:this.#transport,holderPublicKey,origin:this.#origin});
      }
      const result=await this.#signer.execute(raw,key,{signal:this.#signal(signal),reconcile});received=true;
      this.#current();
      if(result.state==='rejected'&&result.code==='version_conflict'){this.#pending=null;return {kind:'rejected',error:failure('version_conflict')};}
      if(result.state!=='committed')throw failure('invalid_response',true);
      if(!(result.transport_response_bytes instanceof Uint8Array))throw failure('invalid_response',true);
      const decoded=prepared.decode_credential_mutation(result.transport_response_bytes,
        {status:200,content_type:'application/json',content_encoding:null,max_bytes:262144},reconcile);
      this.#pending=null;return {kind:'committed',...decoded};
    } catch(error) {
      const uncertain=received || /** @type {any} */(error)?.dispatched===true;
      if(uncertain)this.#pending={raw,key};
      const known=['unsupported_operation','capability_unavailable','user_denied','cancelled','disposed','preparation_failed'];
      const code=uncertain?'outcome_unknown':known.includes(/** @type {any} */(error)?.code)?/** @type {any} */(error).code:'authorization_failed';
      return {kind:uncertain?'outcome_unknown':'not_dispatched',error:failure(code,uncertain)};
    } finally {this.#busy=false;}
  }
  dispose(){if(this.#disposed)return;this.#disposed=true;this.#credential='';this.#controller.abort();this.#signer?.dispose();for(const release of this.#releases)release();globalThis.removeEventListener?.('pagehide',this.#leaving);}
}
