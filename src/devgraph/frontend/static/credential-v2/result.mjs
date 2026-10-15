/** One admission boundary for SDK and Kanban; semantics are defined in Rust. */
export function decodeCredentialResult(prepared, response, reconcile = false) {
  if (!(response?.transport_response_bytes instanceof Uint8Array)
    || !response.transport_response_metadata) throw new Error('invalid_response');
  try {
    const decoded = prepared.decode_credential_mutation(response.transport_response_bytes,
      response.transport_response_metadata, reconcile);
    return {state: 'committed', ...decoded};
  } catch (error) {
    // Only the initial execute response can prove rejection. A failed status
    // lookup says nothing about whether the original request committed.
    if (!reconcile && error?.kind === 'rejected' && error?.code === 'http_rejected')
      return {state: 'rejected', code: response.transport_response_metadata.status === 412
        ? 'version_conflict' : 'request_rejected', status: response.transport_response_metadata.status};
    throw error;
  }
}
