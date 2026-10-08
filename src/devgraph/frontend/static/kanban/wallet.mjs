/** Application-owned orchestration. Wallet never receives Kanban operations or transport RPCs. */
const load = async () => {
  const [{DevgraphCredentialClient}, {createDevgraphHttpTransport}] = await Promise.all([
    import('/assets/credential-v2/coordinator.mjs'), import('/assets/credential-v2/transport.mjs'),
  ]);
  return {DevgraphCredentialClient, createDevgraphHttpTransport};
};
import {prepareProviderSetup} from '../credential-v2/provider-setup.mjs';
import {subjectPublicKey} from '../credential-v2/subject.mjs';
export {subjectPublicKey};
const hex = bytes => [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
const error = message => Object.assign(Error(message), {dispatched: false});
export class Wallet {
  constructor(provider = globalThis.castaliaWallet, modules = load, origin = globalThis.location?.origin) {
    this.provider = provider; this.modules = modules; this.origin = origin;
    this.connection = null; this.client = null; this.busy = false; this.generation = 0; this.setup = null;
  }
  async prepareSetup() {
    this.dispose();
    const generation = this.generation;
    const current = () => {if(generation !== this.generation) throw error("Wallet setup changed. Try again.");};
    if (typeof this.provider?.getCapabilities !== 'function'
      || typeof this.provider?.presentCredential !== 'function'
      || !(await this.provider.getCapabilities()).includes('credential_presentation_v2'))
      throw error('A wallet supporting credential-bound approvals is required. Reading remains available.');
    const {DevgraphCredentialClient, createDevgraphHttpTransport} = await this.modules();
    const transport = createDevgraphHttpTransport({origin: this.origin});
    const supported = await transport.getCapabilities();
    if (supported?.schema !== 'devgraph.credential-transport-capabilities.v2'
      || !supported.operations?.includes('devgraph.work.workflow.transition.v2'))
      throw error('Workflow changes are pending Devgraph authority support. Reading remains available.');
    const setup = await prepareProviderSetup({provider:this.provider, transport, origin:this.origin, current});
    current(); this.setup = setup; this.prepared = {DevgraphCredentialClient, transport, supported, current};
    return setup;
  }
  async connect() {
    if(!this.setup) throw error('Load Wallet setup before connecting.');
    const {DevgraphCredentialClient, transport, supported, current} = this.prepared;
    // Must start directly in this click, before subject reads or network awaits.
    const connection = this.setup.connect();
    this.client?.dispose(); this.client = null; this.connection = null;
    await connection; current();
    const subject = await this.provider.getSubject(); current();
    const holderPublicKey = subjectPublicKey(subject);
    const key = Uint8Array.from(holderPublicKey.match(/../g), value => parseInt(value, 16));
    const actor = 'pubkey:sha256:' + hex(await crypto.subtle.digest('SHA-256', key));
    const profile = hex(await crypto.subtle.digest('SHA-256',
      new TextEncoder().encode('devgraph.credential-client.v2\0' + this.origin)));
    current();
    this.client = new DevgraphCredentialClient({provider: this.provider, transport,
      holderPublicKey, origin: this.origin});
    this.connection = Object.freeze({actor_id: actor, receiver_profile: profile,
      operations: [...supported.operations]});
    return this.connection;
  }
  async dispose() {this.generation++; this.setup = null; this.prepared = null; this.client?.dispose(); this.client = null; this.connection = null;}
  async execute(raw, key, {reconcile = false} = {}) {
    if (this.busy) throw error('Finish the pending change first.');
    if (!this.client || !this.connection) throw error('Connect a compatible wallet before changing work.');
    const request = JSON.parse(raw);
    if (!this.connection.operations.includes(`devgraph.work.${request.operation}.${request.schema.endsWith("v2") ? "v2" : "v1"}`))
      throw error('This operation is not supported by the current Devgraph authority.');
    this.busy = true;
    try {
      const result = await this.client.execute(raw, key, {reconcile});
      if (result.state === 'rejected' && result.code === 'version_conflict')
        throw Object.assign(error('This item changed while you were reviewing it. The latest version has been loaded.'), {status: 412});
      if (result.state !== 'committed' || !result.receipt?.receipt_id
        || result.receipt.operation !== `devgraph.work.${request.operation}.${request.schema.endsWith("v2") ? "v2" : "v1"}`
        || result.receipt.subject_label !== request.kind || result.receipt.subject_id !== request.id
        || typeof result.receipt.duplicate !== 'boolean')
        throw Object.assign(Error('The original outcome remains unknown. Keep this request.'), {dispatched: true});
      return result;
    } finally {this.busy = false;}
  }
  async reconcile(raw, key) {return this.execute(raw, key, {reconcile: true});}
}
