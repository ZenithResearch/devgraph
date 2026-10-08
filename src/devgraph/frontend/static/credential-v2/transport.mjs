/** Same-origin Devgraph transport; the webpage never receives S3/secS/admin keys. */
export function createDevgraphHttpTransport({origin = globalThis.location?.origin,
  fetch: request = globalThis.fetch} = {}) {
  const parsed = new URL(origin);
  if (parsed.origin !== origin || !['http:', 'https:'].includes(parsed.protocol))
    throw new Error('invalid_origin');
  async function call(action, body, {signal} = {}) {
    const response = await request(`${origin}/credential-work/v2/${action}`, {
      method: 'POST', mode: 'same-origin', credentials: 'omit', redirect: 'error', signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    if (response.url && new URL(response.url).origin !== origin) throw new Error('invalid_origin');
    const conflict = action === 'execute' && response.status === 412;
    if (!response.ok && !conflict) throw new Error('credential_transport_denied');
    if (response.headers.get('content-type')?.split(';')[0] !== (conflict ? 'application/problem+json' : 'application/json'))
      throw new Error('invalid_content_type');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('missing_response_body');
    let size = 0; const chunks = [];
    try {
      for (;;) {
        const {value, done} = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > 262144) throw new Error('response_too_large');
        chunks.push(value);
      }
    } catch (error) { await reader.cancel(); throw error; }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const part of chunks) {bytes.set(part, offset); offset += part.byteLength;}
    const result = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    if (conflict) {
      if (result.type !== 'about:blank' || result.status !== 412 || result.title !== 'Version precondition failed')
        throw new Error('invalid_problem');
      return {state: 'rejected', code: 'version_conflict', status: 412};
    }
    if (action === 'execute' || action === 'status') {
      if (!result || typeof result !== 'object' || Array.isArray(result)
        || Object.hasOwn(result, 'transport_response_bytes')) throw new Error('invalid_response');
      return {...result, transport_response_bytes: bytes};
    }
    return result;
  }
  return Object.freeze({getCapabilities: () => call('capabilities', {}),
    getProviderProfile: () => call('provider-profile', {}),
    prepareCredential: (value, options) => call('prepare', value, options),
    executeCredential: (value, options) => call('execute', value, options),
    reconcileOperation: (value, options) => call('status', value, options)});
}
