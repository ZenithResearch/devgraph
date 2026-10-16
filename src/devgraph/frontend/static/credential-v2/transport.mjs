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
    const mutation = action === 'execute' || action === 'status';
    if (!mutation && !response.ok) throw new Error('credential_transport_denied');
    if (!mutation && response.headers.get('content-type')?.split(';')[0] !== 'application/json')
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
    // Keep HTTP metadata and original bytes intact for the shared Rust decoder.
    // JSON.parse here would hide duplicate keys and cannot establish a commit.
    if (mutation) return {transport_response_bytes: bytes, transport_response_metadata: {
      status: response.status, content_type: response.headers.get('content-type'),
      content_encoding: response.headers.get('content-encoding'), max_bytes: 262144,
    }};
    return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
  }
  return Object.freeze({getCapabilities: () => call('capabilities', {}),
    getProviderProfile: () => call('provider-profile', {}),
    prepareCredential: (value, options) => call('prepare', value, options),
    executeCredential: (value, options) => call('execute', value, options),
    reconcileOperation: (value, options) => call('status', value, options)});
}
