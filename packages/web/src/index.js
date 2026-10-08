import {GenericClient} from './generic.js';
// Protocol parsing, request validation and domain decoding live in shared Rust.
// This module owns asynchronous browser resources and never holds WASM views.
/** @typedef {import('./domain.js').WorkKind} WorkKind */
/** @typedef {import('./domain.js').Work} Work */
/** @typedef {import('./domain.js').WorkRequestInput} WorkRequestInput */
/** @typedef {import('./domain.js').MutationResult | import('./domain.js').ProgressMutationResult} MutationResult */
/** @typedef {import('./domain.js').ExpectedResult} ExpectedResult */
/** @typedef {import('./domain.js').CypherResult} CypherResult */
/** @typedef {{readonly expires_at:number}} ReadContext */
/** @typedef {{readonly connection_id:string,readonly actor_id:string,readonly receiver_profile:string,readonly stable_issuer:string,readonly audience:string,readonly origin:string,readonly capabilities:readonly string[]}} ConnectionProfile */
/** @typedef {{read_context:ReadContext,signal?:AbortSignal}} ReadOptions */
/** @typedef {{kind:WorkKind,include_archived?:boolean,descending?:boolean,limit?:number,after_id?:string}} WorkFilter */
/** @typedef {'children'|'parent'|'dependencies'|'dependents'|'blockers'|'blocked'} Relationship */
/** @typedef {{kind:'committed'} & MutationResult | {kind:'not_dispatched'|'rejected'|'outcome_unknown',error:DevgraphError}} MutationOutcome */
/** @typedef {{readonly operation:import('./domain.js').NamedWorkOperation,readonly resources:readonly string[],readonly request_digest_sha256:string,readonly idempotency_key_digest_sha256:string,readonly request:WorkRequestInput}} PreparedSummary */
const ABI = 'devgraph.web.v1';
const ORIGIN = 'http://127.0.0.1:8080';
const AUDIENCE = 'devgraph://receiver-local';
const CHUNK = 49152;
const FRAME = 131072;
const SINGLE = 8 * 1024 * 1024;
const PAGE = 16 * 1024 * 1024;
const IDENTITY_FIELDS = /** @type {const} */ (['actor_id','receiver_profile','stable_issuer','audience','origin']);
const ERROR_CODES = new Set((
  'abi_mismatch initialization_failed capability_unavailable connection_failed connection_lost '
  + 'connection_binding_changed connection_disposed authorization_changed authorization_expired '
  + 'authorization_consumed authority_changed authority_denied actor_changed cancelled disposed '
  + 'timeout bridge_failed provider_denied user_denied protocol_error resource_limit invalid_chunk '
  + 'invalid_stream invalid_handshake invalid_read_context read_context_expired invalid_receiver_profile '
  + 'invalid_authorization invalid_request invalid_work_request invalid_idempotency_key invalid_js_value '
  + 'invalid_utf16 input_too_large input_too_deep invalid_read invalid_response http_rejected '
  + 'duplicate_json_key invalid_json invalid_json_number invalid_json_string invalid_content_type '
  + 'invalid_error_content_type invalid_http_status invalid_problem response_too_deep response_too_large '
  + 'invalid_response_limit unsupported_content_encoding invalid_work invalid_work_page invalid_parent_page '
  + 'work_identity_mismatch mutation_result_mismatch mutation_work_mismatch invalid_mutation_result '
  + 'invalid_read_descriptor read_status_mismatch page_limit_mismatch page_order_mismatch '
  + 'cypher_result_mismatch invalid_cypher_request non_advancing_page truncated_response invalid_sequence '
  + 'invalid_frame invalid_message unsupported_version invalid_origin unapproved_origin unapproved_extension '
  + 'unsafe_profile receiver_profile_changed issuer_changed issuer_timeout issuer_unavailable '
  + 'invalid_identity invalid_presentation invalid_authority invalid_clock randomness_unavailable '
  + 'read_authority_unavailable transport_error transport_unavailable transport_timeout port_disconnected stream_closed '
  + 'concurrent_pull read_limit mutation_limit control_limit duplicate_or_exhausted_id already_connected '
  + 'outcome_unknown rejected'
).split(' '));
const capability = Symbol('devgraph client');
/** @type {Promise<any> | undefined} */
let defaultModule;
/** @type {WeakMap<object, any>} */
const connections = new WeakMap();
/** @type {WeakMap<object, any>} */
const readContexts = new WeakMap();

/** An allowlisted failure without raw request, credentials or provider causes.
 * In a mutation outcome, dispatched=false proves no dispatch; true reports dispatch
 * or dispatch risk; null means no reliable report. None proves a graph commit.
 */
export class DevgraphError extends Error {
  /** @param {string} code @param {boolean | null} [dispatched] */
  constructor(code, dispatched = null) {
    super(code);
    this.name = 'DevgraphError';
    this.code = code;
    this.dispatched = dispatched;
  }
}

/**
 * Initialize on demand. Importing this package does not touch browser globals or
 * fetch WASM. Concurrent default initialization shares a promise; failures retry.
 * @param {{wasm?: URL | string | Uint8Array | ArrayBuffer | WebAssembly.Module}} [options]
 */
export async function initialize(options = {}) {
  const load = async () => {
    const {createBindings} = await import('./internal/devgraph_web_factory.js');
    const bindings = createBindings();
    await bindings.default(options.wasm === undefined ? undefined : {module_or_path: options.wasm});
    if (bindings.abi_version() !== ABI) throw new DevgraphError('abi_mismatch');
    return bindings;
  };
  let wasm;
  try {
    if (options.wasm !== undefined) wasm = await load();
    else {
      defaultModule ??= load().catch(error => { defaultModule = undefined; throw error; });
      wasm = await defaultModule;
    }
  } catch (error) { throw safeError(error, 'initialization_failed'); }
  return new Runtime(capability, wasm);
}

/** @param {any} error @param {string} fallback */
function safeError(error, fallback) {
  // Rust and extension use bounded fixed codes, never arbitrary error messages.
  let code, dispatched;
  try {
    code = Object.getOwnPropertyDescriptor(error,'code')?.value;
    dispatched = Object.getOwnPropertyDescriptor(error,'dispatched')?.value;
  } catch { /* Never invoke error getters or retain an untrusted exception. */ }
  return new DevgraphError(typeof code === 'string' && ERROR_CODES.has(code) ? code : fallback,
    typeof dispatched === 'boolean' ? dispatched : null);
}
/** @param {any} value */
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
/** @param {any} value */
function handle(value) { return typeof value === 'string' && /^[\x21-\x7e]{1,128}$/.test(value); }
/** @param {any} value */
function profile(value) {
  const fields=['connection_id',...IDENTITY_FIELDS,'capabilities'];
  return object(value) && Object.keys(value).length===fields.length && Object.keys(value).every(key=>fields.includes(key))
    && handle(value.connection_id) && typeof value.actor_id === 'string' && value.actor_id.length>0 && value.actor_id.length<=256
    && /^[0-9a-f]{64}$/.test(value.receiver_profile) && value.origin === ORIGIN
    && value.audience === AUDIENCE && typeof value.stable_issuer === 'string' && value.stable_issuer.length>0 && value.stable_issuer.length<=256
    && Array.isArray(value.capabilities) && [2,3,4].includes(value.capabilities.length) && value.capabilities.every(/** @param {unknown} c */ c=>typeof c==='string'&&['read','work.v1','workflow.v1','progress.v1'].includes(c)) && new Set(value.capabilities).size===value.capabilities.length && value.capabilities.includes('work.v1')
    && value.capabilities.includes('read');
}
/** @param {any} value */
function freeze(value) {
  if (object(value) || Array.isArray(value)) {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}
/** @param {Uint8Array} bytes */
function encode(bytes) {
  let binary = '';
  for (let at = 0; at < bytes.length; at += 8192)
    binary += String.fromCharCode(...bytes.subarray(at, at + 8192));
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}
/** @param {any} text */
function decode(text) {
  if (typeof text !== 'string' || text.length > 65536 || !/^[A-Za-z0-9_-]*$/.test(text))
    throw new DevgraphError('invalid_chunk');
  let raw;
  try { raw = Uint8Array.from(atob(text.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0)); }
  catch { throw new DevgraphError('invalid_chunk'); }
  if (raw.length > CHUNK || encode(raw) !== text) throw new DevgraphError('invalid_chunk');
  return raw;
}
/** @param {AbortSignal | undefined} signal */
function checkAbort(signal) { if (signal?.aborted) throw new DevgraphError('cancelled', false); }

/** A bounded handshake/disposal RPC. Late handshakes retain their cleanup owner.
 * @param {any} invoke @param {any} payload @param {number} [timeout] @param {AbortSignal} [signal]
 */
async function control(invoke, payload, timeout = 10000, signal) {
  let finished = false;
  /** @type {ReturnType<typeof setTimeout> | undefined} */ let timer;
  /** @type {() => void} */ let abort = () => {};
  try {
    return await new Promise((resolve,reject) => {
      const fail = (/** @type {string} */ code) => {if(!finished){finished=true;reject(new DevgraphError(code,false));}};
      abort=()=>fail('disposed');
      signal?.addEventListener('abort',abort,{once:true});
      if(signal?.aborted){abort();return;}
      timer=setTimeout(()=>fail('timeout'),timeout);
      Promise.resolve().then(()=>{
        if(finished) throw new DevgraphError(signal?.aborted?'disposed':'timeout',false);
        return invoke(payload);
      }).then(result=>{
        if(finished){
          if(payload.action==='connect'&&handle(result?.connection_id)) void disposeRemote(invoke,result.connection_id);
          return;
        }
        finished=true;resolve(result);
      },error=>{if(!finished){finished=true;reject(safeError(error,'connection_failed'));}});
    });
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}

/** @param {any} invoke @param {string | null} connectionId */
async function disposeRemote(invoke, connectionId) {
  if(connectionId===null)return;
  try {await control(invoke,{v:1,request_id:crypto.randomUUID(),action:'dispose',connection_id:connectionId},1000);}
  catch { /* Local revocation does not depend on a functioning old port. */ }
}

/** Connect to the installed, approved Castalia developer preview. */
export async function connectCastalia() {
  const provider = /** @type {any} */ (globalThis).castaliaWallet;
  if (!provider || typeof provider.devgraph !== 'function') throw new DevgraphError('capability_unavailable', false);
  const invoke = provider.devgraph.bind(provider);
  let result;
  try { result = await control(invoke,{v: 1, request_id: crypto.randomUUID(), action: 'connect'}); }
  catch (error) { throw safeError(error, 'connection_failed'); }
  if (!profile(result)) {
    if(handle(result?.connection_id)) await disposeRemote(invoke,result.connection_id);
    throw new DevgraphError('invalid_handshake', false);
  }
  return new CastaliaConnection(capability, invoke, result);
}

export class CastaliaConnection {
  /** @param {symbol} token @param {any} invoke @param {any} binding */
  constructor(token, invoke, binding) {
    if (token !== capability) throw new DevgraphError('invalid_connection');
    const anchor=freeze(structuredClone(binding));
    connections.set(this, {invoke,binding:anchor,anchor,phase:'ready',ownedId:binding.connection_id,epoch:0,active:new Set(),reconnect:null,disposal:null});
  }
  /** @returns {ConnectionProfile} */
  get profile() { return connections.get(this).binding; }
  /** @param {{signal?: AbortSignal}} [options] @returns {Promise<ReadContext>} */
  async requestReadAccess(options = {}) {
    const state = connections.get(this);
    const epoch = state.epoch;
    const connectionId = state.ownedId;
    const result = await call(this, 'request_read_access', {}, options.signal, 120000);
    if(state.phase!=='ready'||state.epoch!==epoch){
      if(handle(result?.read_context))cancel(this,result.read_context,connectionId);
      throw new DevgraphError(state.phase==='disposed'?'disposed':'connection_lost',false);
    }
    if (!handle(result?.read_context) || !Number.isSafeInteger(result.expires_at)
      || result.expires_at <= Date.now() / 1000 || result.expires_at > Date.now() / 1000 + 901)
      throw new DevgraphError('invalid_read_context');
    const context = Object.freeze({expires_at: result.expires_at});
    readContexts.set(context, {connection: this, epoch, handle: result.read_context});
    return context;
  }
  /** Reconnect explicitly against the original identity. Transient failure permits another explicit attempt.
   * @returns {Promise<CastaliaConnection>}
   */
  async reconnect() {
    const state = connections.get(this);
    if(state.phase==='disposed')throw new DevgraphError('disposed',false);
    if(state.reconnect)return state.reconnect.promise;
    state.phase='reconnecting';state.epoch++;
    invalidate(state,'connection_lost');
    const oldId=state.ownedId;state.ownedId=null;
    const attempt={epoch:state.epoch,controller:new AbortController(),promise:/** @type {Promise<CastaliaConnection> | undefined} */(undefined)};
    state.reconnect=attempt;
    const current=()=>{
      if(state.phase==='disposed')throw new DevgraphError('disposed',false);
      if(state.phase!=='reconnecting'||state.reconnect!==attempt||state.epoch!==attempt.epoch)
        throw new DevgraphError('connection_lost',false);
    };
    attempt.promise=(async()=>{
      /** @type {any} */ let candidate;
      try {
        if(oldId!==null){
          try {await control(state.invoke,{v:1,request_id:crypto.randomUUID(),action:'dispose',connection_id:oldId},1000,attempt.controller.signal);}
          catch(error){if(attempt.controller.signal.aborted)throw error;}
        }
        current();
        candidate=await control(state.invoke,{v:1,request_id:crypto.randomUUID(),action:'connect'},10000,attempt.controller.signal);
        current();
        if(!profile(candidate)||IDENTITY_FIELDS.some(key=>state.anchor[key]!==candidate[key]))
          throw new DevgraphError('connection_binding_changed',false);
        state.binding=freeze(structuredClone(candidate));state.ownedId=candidate.connection_id;state.phase='ready';
        candidate=undefined;
        return this;
      }catch(error){
        if(state.reconnect===attempt&&state.phase!=='disposed')state.phase='disconnected';
        throw safeError(error,'connection_failed');
      }finally{
        if(handle(candidate?.connection_id))await disposeRemote(state.invoke,candidate.connection_id);
        if(state.reconnect===attempt)state.reconnect=null;
      }
    })();
    return attempt.promise;
  }
  /** Disposal is terminal, including when a reconnect handshake is pending.
   * @returns {Promise<void>}
   */
  async dispose() {
    const state=connections.get(this);
    if(state.phase==='disposed')return state.disposal;
    state.phase='disposed';state.epoch++;
    state.reconnect?.controller.abort();
    invalidate(state,'disposed');
    const connectionId=state.ownedId;state.ownedId=null;
    state.disposal=disposeRemote(state.invoke,connectionId);
    return state.disposal;
  }
}

/** @param {any} state @param {string} code */
function invalidate(state, code) {
  for (const stop of state.active) stop(code);
  state.active.clear();
}

/** @param {CastaliaConnection} connection @param {string} target @param {string | null} connectionId */
function cancel(connection, target, connectionId) {
  if(connectionId===null)return;
  const state = connections.get(connection);
  void control(state.invoke,{v:1,request_id:crypto.randomUUID(),action:'cancel',connection_id:connectionId,target_id:target},1000).catch(() => {});
}

/** @param {CastaliaConnection} connection @param {string} action @param {any} fields @param {AbortSignal} [signal] @param {number} [timeout] @param {string} [expectedConnectionId] */
async function call(connection, action, fields, signal, timeout = 30000, expectedConnectionId) {
  checkAbort(signal);
  const state = connections.get(connection);
  if (!state || state.phase==='disposed') throw new DevgraphError('disposed', false);
  if(state.phase!=='ready'||(expectedConnectionId!==undefined&&state.ownedId!==expectedConnectionId))throw new DevgraphError('connection_lost',false);
  const epoch = state.epoch;
  const connectionId = state.ownedId;
  const id = crypto.randomUUID();
  let finished = false;
  let sent = false;
  /** @type {ReturnType<typeof setTimeout> | undefined} */ let timer;
  /** @type {(code:string) => void} */ let stop = () => {};
  /** @type {() => void} */ let abort = () => {};
  try {
    return await new Promise((resolve, reject) => {
      const fail = (/** @type {string} */ code) => {
        if (finished) return;
        finished = true;
        cancel(connection, id, connectionId);
        reject(new DevgraphError(code, action === 'execute' && sent ? null : false));
      };
      stop = code => fail(code);
      abort = () => fail('cancelled');
      state.active.add(stop);
      signal?.addEventListener('abort', abort, {once:true});
      timer = setTimeout(() => fail('timeout'), timeout);
      Promise.resolve().then(() => {
        if (finished || state.phase!=='ready' || state.epoch !== epoch) throw new DevgraphError('cancelled',false);
        sent = true;
        return state.invoke({v:1,request_id:id,action,connection_id:connectionId,...fields});
      }).then(result => {
        if (finished || state.epoch !== epoch || state.phase!=='ready') {
          if (handle(result?.stream_id)) cancel(connection, result.stream_id, connectionId);
          if (handle(result?.authorization_id)) cancel(connection, result.authorization_id, connectionId);
          if (handle(result?.read_context)) cancel(connection, result.read_context, connectionId);
          if (!finished) fail('connection_binding_changed');
          return;
        }
        finished = true;
        resolve(result);
      }, error => {
        if (finished) return;
        finished = true;
        reject(safeError(error, 'bridge_failed'));
      });
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    state.active.delete(stop);
  }
}

/** @param {CastaliaConnection} connection @param {string} connectionId @param {any} header @param {number} maximum @param {boolean} dispatched @param {AbortSignal} [signal] @param {() => void} [onValidated] */
async function consume(connection, connectionId, header, maximum, dispatched, signal, onValidated) {
  let total = 0;
  let seq = 0;
  const chunks = [];
  const deadline = Date.now() + 30000;
  try {
    if (!object(header) || !handle(header.stream_id) || !Number.isInteger(header.status)
      || header.status < 100 || header.status > 599 || typeof header.content_type !== 'string'
      || ![null, undefined, '', 'identity'].includes(header.content_encoding)
      || !Number.isSafeInteger(header.limit) || header.limit < 0 || header.limit > maximum
      || header.dispatched!==dispatched) throw new DevgraphError('invalid_stream');
    const cap = header.status >= 400 ? Math.min(maximum, 65536) : maximum;
    if (header.content_length !== null && header.content_length !== undefined) {
      if (typeof header.content_length !== 'string' || !/^[0-9]{1,20}$/.test(header.content_length)) throw new DevgraphError('invalid_stream');
      if (BigInt(header.content_length) > BigInt(cap)) throw new DevgraphError('resource_limit');
    }
    onValidated?.();
    while (true) {
      checkAbort(signal);
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new DevgraphError('timeout');
      const part = await call(connection, 'pull', {stream_id:header.stream_id,ack_seq:seq - 1}, signal, remaining, connectionId);
      if (!object(part) || part.stream_id !== header.stream_id || part.seq !== seq
        || typeof part.done !== 'boolean' || !Number.isSafeInteger(part.total)
        || Object.keys(part).some(key=>!['stream_id','seq','total','chunk_b64','done'].includes(key))
        || typeof part.chunk_b64 !== 'string' || part.chunk_b64.length > 65536
        || new TextEncoder().encode(JSON.stringify(part)).length > FRAME) throw new DevgraphError('invalid_chunk');
      const bytes = decode(part.chunk_b64);
      if (part.total !== total + bytes.length || part.total > cap || part.total > header.limit
        || (!part.done && bytes.length === 0)) throw new DevgraphError('resource_limit');
      total = part.total;
      chunks.push(bytes);
      seq++;
      if (part.done) break;
    }
    if (header.content_length != null && BigInt(header.content_length) !== BigInt(total)) throw new DevgraphError('truncated_response');
    const raw = new Uint8Array(total);
    let offset = 0;
    for (const bytes of chunks) { raw.set(bytes, offset); offset += bytes.length; }
    return raw;
  } finally { if(handle(header?.stream_id))cancel(connection, header.stream_id, connectionId); }
}

export class Runtime {
  #wasm;
  #disposed = false;
  #clients = new Set();
  /** @param {symbol} token @param {any} wasm */
  constructor(token, wasm) { if (token !== capability) throw new DevgraphError('invalid_runtime'); this.#wasm = wasm; }
  /** @param {{connection: CastaliaConnection}} options */
  createClient(options) {
    if (this.#disposed) throw new DevgraphError('disposed', false);
    const binding = connections.get(options.connection)?.binding;
    if (!binding) throw new DevgraphError('invalid_connection');
    if (this.#wasm.profile_digest(binding.stable_issuer) !== binding.receiver_profile) throw new DevgraphError('invalid_receiver_profile');
    const client = new Client(capability, this.#wasm, options.connection, () => this.#clients.delete(client));
    this.#clients.add(client);
    return client;
  }
  /** Connect using Devgraph HTTP reads and generic Wallet approval for mutations.
   * @param {import('./generic.js').GenericOptions} [options]
   */
  connectDevgraph(options = {}) {
    if (this.#disposed) throw new DevgraphError('disposed', false);
    const client = new GenericClient(this.#wasm, options);
    this.#clients.add(client);
    return client;
  }
  dispose() { this.#disposed = true; for (const client of this.#clients) client.dispose(); this.#clients.clear(); this.#wasm = null; }
}

export class Client {
  #wasm;
  #connection;
  #disposed = false;
  #prepared = new Set();
  #controller = new AbortController();
  #onDispose;
  /** @param {symbol} token @param {any} wasm @param {CastaliaConnection} connection @param {() => void} onDispose */
  constructor(token, wasm, connection, onDispose) {
    if (token !== capability) throw new DevgraphError('invalid_client');
    this.#wasm = wasm; this.#connection = connection; this.#onDispose = onDispose;
  }
  /** @param {WorkRequestInput} request @param {{idempotency_key:string}} options */
  prepare(request, options) { return this.#prepare(() => this.#wasm.prepare_value(request, options.idempotency_key), options.idempotency_key); }
  /** @param {Uint8Array} bytes @param {{idempotency_key:string}} options */
  prepareBytes(bytes, options) {
    if (!(bytes instanceof Uint8Array)) throw new DevgraphError('invalid_request');
    return this.#prepare(() => this.#wasm.prepare_bytes(bytes, options.idempotency_key), options.idempotency_key);
  }
  /** @param {() => any} factory @param {string} key */
  #prepare(factory, key) {
    if (this.#disposed) throw new DevgraphError('disposed', false);
    let raw;
    try { raw = factory(); } catch (error) { throw safeError(error, 'invalid_request'); }
    const prepared = new PreparedOperation(capability, raw, this.#connection, this.#controller.signal, key, () => this.#prepared.delete(prepared));
    this.#prepared.add(prepared);
    return prepared;
  }
  /** @param {any} input @param {{read_context:object,signal?:AbortSignal}} options */
  async #read(input, options) {
    if (this.#disposed) throw new DevgraphError('disposed', false);
    const context = readContexts.get(options.read_context);
    const state = connections.get(this.#connection);
    if (!context || context.connection !== this.#connection || context.epoch !== state.epoch
      || /** @type {any} */ (options.read_context).expires_at <= Date.now() / 1000)
      throw new DevgraphError('read_context_expired', false);
    const connectionId=state.ownedId;
    const signal = options.signal ? AbortSignal.any([options.signal, this.#controller.signal]) : this.#controller.signal;
    checkAbort(signal);
    let descriptor;
    try { descriptor = this.#wasm.read_descriptor(input); } catch (error) { throw safeError(error, 'invalid_read'); }
    // Freeze the checked, normalized descriptor before the first asynchronous step.
    const normalized = descriptor.request;
    const maximum = input.kind === 'cypher' ? 262144 : ['get_work','get_todo'].includes(input.kind) ? SINGLE : PAGE;
    const header = await call(this.#connection, 'read', {request:normalized,read_context:context.handle}, signal);
    const raw = await consume(this.#connection, connectionId, header, maximum, false, signal);
    try { return this.#wasm.decode_read(raw, {status:header.status,content_type:header.content_type,content_encoding:header.content_encoding,max_bytes:maximum}, normalized); }
    catch (error) { throw safeError(error, 'invalid_response'); }
  }
  /** @param {WorkKind} kind @param {string} id @param {ReadOptions} options @returns {Promise<import('./domain.js').ProgressWork>} */
  async getTodo(kind, id, options) { return this.#read({kind:'get_todo',work_kind:kind,id}, options); }
  /** @param {WorkKind} kind @param {string} id @param {ReadOptions} options @returns {Promise<Work>} */
  async getWork(kind, id, options) { return this.#read({kind:'get_work',work_kind:kind,id}, options); }
  /** @param {WorkFilter} filter @param {ReadOptions} options @returns {AsyncGenerator<Work,void,unknown>} */
  async *iterateWork(filter, options) {
    const stable = structuredClone(filter);
    let after = stable.after_id;
    const limit = stable.limit ?? 50;
    while (true) {
      const input = {kind:'list_work',work_kind:stable.kind,filters:{include_archived:stable.include_archived ?? false,descending:stable.descending ?? false},limit,...(after === undefined ? {} : {after_id:after})};
      const page = await this.#read(input, options);
      if (!Array.isArray(page.items) || page.items.length > limit) throw new DevgraphError('invalid_page');
      for (const item of page.items) {
        if (after !== undefined && (stable.descending ? item.id >= after : item.id <= after)) throw new DevgraphError('non_advancing_page');
        after = item.id;
        yield item;
      }
      if (page.items.length < limit) return;
    }
  }
  /** @param {{kind:WorkKind,id:string}} subject @param {Relationship} relation_kind @param {ReadOptions & {limit?:number,after_resource?:string}} options @returns {AsyncGenerator<Work,void,unknown>} */
  async *iterateRelations(subject, relation_kind, options) {
    const stable = structuredClone(subject);
    let after = options.after_resource;
    const limit = options.limit ?? 50;
    while (true) {
      const input = {kind:'list_relations',subject:stable,relation_kind,limit,...(after === undefined ? {} : {after_resource:after})};
      const page = await this.#read(input, options);
      if (!Array.isArray(page.items) || page.items.length > limit) throw new DevgraphError('invalid_page');
      for (const item of page.items) {
        const resource = `${item.kind}/${item.id}`;
        if (after !== undefined && resource <= after) throw new DevgraphError('non_advancing_page');
        after = resource;
        yield item;
      }
      if (page.items.length < limit) return;
    }
  }
  /** @param {Uint8Array} request_bytes @param {ExpectedResult} expected_result @param {ReadOptions} options @returns {Promise<CypherResult>} */
  async cypher(request_bytes, expected_result, options) {
    if (!(request_bytes instanceof Uint8Array) || request_bytes.length > 32768) throw new DevgraphError('invalid_cypher_request');
    return this.#read({kind:'cypher',request_b64:encode(request_bytes),expected_result:structuredClone(expected_result)}, options);
  }
  dispose() {
    this.#disposed = true; this.#controller.abort();
    for (const prepared of this.#prepared) prepared.dispose();
    this.#prepared.clear();
    this.#wasm = null;
    this.#onDispose();
  }
}

export class PreparedOperation {
  #raw;
  #connection;
  #binding;
  #disposed = false;
  #summary;
  #canonical;
  #controller = new AbortController();
  #parent;
  #key;
  #onDispose;
  #attempts = new Set();
  /** @param {symbol} token @param {any} raw @param {CastaliaConnection} connection @param {AbortSignal} parent @param {string} key @param {() => void} onDispose */
  constructor(token, raw, connection, parent, key, onDispose) {
    if (token !== capability) throw new DevgraphError('invalid_prepared');
    this.#raw = raw; this.#connection = connection; this.#parent = parent; this.#key = key; this.#onDispose = onDispose;
    this.#binding = connection.profile;
    this.#canonical = raw.canonical_bytes().slice();
    this.#summary = freeze(raw.summary());
  }
  /** @returns {PreparedSummary} */
  get summary() { return this.#summary; }
  /** @returns {Uint8Array} */
  get canonical_bytes() { return this.#canonical.slice(); }
  /** @param {{signal?:AbortSignal}} [options] */
  async authorize(options = {}) {
    if (this.#disposed) throw new DevgraphError('disposed', false);
    if (IDENTITY_FIELDS.some(k => this.#binding[k] !== this.#connection.profile[k]))
      throw new DevgraphError('connection_binding_changed', false);
    const signal = AbortSignal.any([this.#controller.signal,this.#parent,...(options.signal ? [options.signal] : [])]);
    const state=connections.get(this.#connection), epoch=state.epoch, connectionId=state.ownedId;
    const authorized = await call(this.#connection, 'authorize', {request_b64:encode(this.#canonical),idempotency_key:this.#key}, signal, 130000);
    if(signal.aborted||state.epoch!==epoch||state.phase!=='ready'){
      if(handle(authorized?.authorization_id))cancel(this.#connection,authorized.authorization_id,connectionId);
      throw new DevgraphError(signal.aborted?'cancelled':state.phase==='disposed'?'disposed':'connection_lost',false);
    }
    if (!handle(authorized?.authorization_id) || !Number.isSafeInteger(authorized.expires_at)
      || authorized.expires_at <= Date.now()/1000 || authorized.expires_at > Date.now()/1000 + 61)
      throw new DevgraphError('invalid_authorization', false);
    const attempt = new AuthorizedAttempt(capability, this.#connection, authorized, this.#raw, signal, () => this.#attempts.delete(attempt));
    this.#attempts.add(attempt);
    return attempt;
  }
  dispose() {
    if (this.#disposed) return;
    this.#disposed = true; this.#controller.abort();
    for (const attempt of this.#attempts) attempt.dispose();
    this.#attempts.clear(); this.#raw.free(); this.#raw = null; this.#canonical = new Uint8Array(); this.#key = ''; this.#onDispose();
  }
}

export class AuthorizedAttempt {
  #connection;
  #authorization;
  #raw;
  #signal;
  #epoch;
  #connectionId;
  #used = false;
  #controller = new AbortController();
  #onFinish;
  /** @param {symbol} token @param {CastaliaConnection} connection @param {any} authorization @param {any} raw @param {AbortSignal} signal @param {() => void} onFinish */
  constructor(token, connection, authorization, raw, signal, onFinish) {
    if (token !== capability) throw new DevgraphError('invalid_authorization');
    this.#connection = connection; this.#authorization = authorization; this.#raw = raw; this.#signal = signal; this.#onFinish = onFinish;
    this.#epoch = connections.get(connection).epoch;
    this.#connectionId = connections.get(connection).ownedId;
  }
  /** A single dispatch. Reauthorization requires another explicit confirmation.
   * @param {{signal?:AbortSignal}} [options]
   * @returns {Promise<MutationOutcome>}
   */
  async execute(options = {}) {
    if (this.#used) return {kind:'not_dispatched',error:new DevgraphError('authorization_consumed',false)};
    this.#used = true;
    const signal = AbortSignal.any([this.#signal,this.#controller.signal,...(options.signal ? [options.signal] : [])]);
    if (signal.aborted || connections.get(this.#connection).epoch !== this.#epoch || this.#authorization.expires_at <= Date.now()/1000) {
      this.dispose();
      return {kind:'not_dispatched',error:new DevgraphError(signal.aborted ? 'cancelled' : 'authorization_expired',false)};
    }
    let responseStarted = false;
    /** @type {boolean | null} */ let dispatched = null;
    try {
      const header = await call(this.#connection, 'execute', {authorization_id:this.#authorization.authorization_id}, signal, 30000, this.#connectionId);
      responseStarted = true;
      const raw = await consume(this.#connection, this.#connectionId, header, SINGLE, true, signal,()=>{dispatched=true;});
      if (signal.aborted) throw new DevgraphError('cancelled');
      let result;
      try { result = this.#raw.decode_mutation(raw, {status:header.status,content_type:header.content_type,content_encoding:header.content_encoding,max_bytes:SINGLE}); }
      catch (error) {
        if (/** @type {any} */ (error)?.kind === 'rejected') {
          const safe=safeError(error,'rejected');safe.dispatched=true;
          return {kind:'rejected',error:safe};
        }
        throw error;
      }
      return {kind:'committed',...result};
    } catch (error) {
      const safe = safeError(error,'invalid_response');
      if(!responseStarted)dispatched=safe.dispatched;
      safe.dispatched=dispatched;
      return {kind:dispatched===false?'not_dispatched':'outcome_unknown',error:safe};
    } finally { cancel(this.#connection, this.#authorization.authorization_id, this.#connectionId); this.#raw = null; this.#onFinish(); }
  }
  dispose() { this.#used = true; this.#controller.abort(); cancel(this.#connection, this.#authorization.authorization_id, this.#connectionId); this.#raw = null; this.#onFinish(); }
}

/** Explicit JSON export uses decimal strings for BigInt; never signed serialization.
 * @param {unknown} value
 */
export function exportJson(value) { return JSON.stringify(value, (_key, item) => typeof item === 'bigint' ? item.toString() : item); }
