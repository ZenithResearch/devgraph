/** Devgraph owns transport and execution. Wallet only presents an attached credential.
 * The transport is an application-owned, trusted composition dependency with
 * prepareCredential, executeCredential and reconcileOperation methods. This
 * module never installs a native host or supplies issuer pins to Wallet.
 */
const CAPABILITY = 'credential_presentation_v2';
export class CredentialClientError extends Error {
  constructor(code, dispatched = false) { super(code); this.code = code; this.dispatched = dispatched; }
}
const fail = (code, dispatched = false) => { throw new CredentialClientError(code, dispatched); };
const clone = value => structuredClone(value);

export class DevgraphCredentialClient {
  #provider; #transport; #holder; #caller; #busy = false; #disposed = false;
  #epoch = 0; #controller = null; #pending = null;
  constructor({provider, transport, holderPublicKey, origin}) {
    if (!/^[0-9a-f]{64}$/.test(holderPublicKey)) fail('invalid_holder');
    const url = new URL(origin);
    if (url.origin !== origin || !['https:', 'http:'].includes(url.protocol)
      || url.protocol === 'http:' && !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname))
      fail('invalid_origin');
    for (const name of ['prepareCredential', 'executeCredential', 'reconcileOperation'])
      if (typeof transport?.[name] !== 'function') fail('transport_unavailable');
    this.#provider = provider; this.#transport = transport; this.#holder = holderPublicKey;
    this.#caller = Object.freeze({kind: 'browser', id: origin});
  }
  async execute(request, idempotencyKey, {signal, reconcile = false} = {}) {
    if (this.#disposed) fail('disposed');
    if (this.#busy) fail('operation_in_progress');
    if (this.#pending && (!reconcile || request !== this.#pending.request
      || idempotencyKey !== this.#pending.idempotency_key)) fail('reconciliation_required', true);
    if (!/^[A-Za-z0-9._~-]{16,128}$/.test(idempotencyKey)) fail('invalid_idempotency_key');
    if (typeof request !== 'string' || new TextEncoder().encode(request).length > 131072)
      fail('invalid_request');
    this.#busy = true;
    const epoch = this.#epoch, controller = new AbortController();
    this.#controller = controller;
    const cancel = () => controller.abort();
    signal?.addEventListener('abort', cancel, {once: true});
    if (signal?.aborted) cancel();
    let dispatched = false;
    const current = () => {
      if (this.#disposed || this.#epoch !== epoch) fail('disposed', dispatched);
      if (controller.signal.aborted) fail('cancelled', dispatched);
    };
    const operation = Object.freeze({request, idempotency_key: idempotencyKey});
    try {
      current();
      if (typeof this.#provider?.getCapabilities !== 'function'
        || typeof this.#provider?.presentCredential !== 'function'
        || !(await this.#provider.getCapabilities()).includes(CAPABILITY))
        fail('capability_unavailable');
      current();
      const prepared = clone(await this.#transport.prepareCredential({
        ...operation, holder_public_key: this.#holder, caller: this.#caller,
      }, {signal: controller.signal}));
      current();
      if (prepared?.schema !== 'castalia.credential-presentation-request.v2')
        fail('invalid_preparation');
      // Keep a separate snapshot: neither an asynchronous provider nor page
      // mutation can substitute credential/disclosure at dispatch.
      const snapshot = clone(prepared);
      const approval = await this.#provider.presentCredential(prepared);
      current();
      if (approval?.state !== 'approved') fail(approval?.state === 'denied'
        ? 'user_denied' : 'capability_unavailable');
      const presentation = clone(approval.presentation);
      if (presentation?.schema !== 'castalia.credential-presentation.v2')
        fail('invalid_presentation');
      current();
      // Preserve reconciliation ownership before crossing the dispatch boundary.
      this.#pending = {...operation, credential: snapshot.credential,
        disclosure: snapshot.disclosure, presentation}; dispatched = true;
      const result = await (reconcile ? this.#transport.reconcileOperation : this.#transport.executeCredential)
        .call(this.#transport, clone(this.#pending), {signal: controller.signal});
      current();
      if (!['committed', 'rejected', 'not_dispatched'].includes(result?.state))
        fail('outcome_unknown', true);
      this.#pending = null;
      return clone(result);
    } catch (error) {
      if (dispatched) fail('outcome_unknown', true);
      if (error instanceof CredentialClientError) throw error;
      fail('preparation_failed');
    } finally {
      signal?.removeEventListener('abort', cancel);
      this.#controller = null; this.#busy = false;
    }
  }
  async reconcile(options = {}) {
    if (this.#busy) fail('operation_in_progress');
    if (!this.#pending) fail('no_pending_operation');
    return this.execute(this.#pending.request, this.#pending.idempotency_key,
      {...options, reconcile: true});
  }
  dispose() {
    this.#disposed = true; this.#epoch++; this.#controller?.abort();
    // The caller retains exact request/key bytes for a fresh client and approval.
  }
}
