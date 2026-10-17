/** Exact public Wallet errors only; never forward arbitrary provider diagnostics. */
const codes = new Map([
  ['wallet_unlock_required', 'wallet_unlock_required'],
  ['wallet_locked', 'wallet_unlock_required'],
  ['user_denied', 'user_denied'],
  ['approval_denied', 'user_denied'],
  ['Wallet not set up or locked for this session', 'wallet_unlock_required'],
  ['wallet is locked', 'wallet_unlock_required'],
  ['origin_not_approved', 'wallet_connection_required'],
  ['wallet_connection_required', 'wallet_connection_required'],
  ['approval_required', 'approval_required'],
]);
/** @type {Record<string,string>} */
const messages = {
  user_denied: 'Wallet approval was cancelled.',
  wallet_unlock_required: 'Unlock Castalia Wallet, then continue with this request.',
  wallet_connection_required: 'Connect Castalia Wallet to this site, then continue with this request.',
  approval_required: 'Open Wallet from the appropriate button to review this request.',
};
/** @param {unknown} error */
export function normalizeWalletError(error) {
  if (!error || !['object', 'function'].includes(typeof error)) return null;
  try {
    for (const field of ['code', 'message']) {
      const descriptor = Object.getOwnPropertyDescriptor(error, field);
      const code = descriptor && 'value' in descriptor && codes.get(descriptor.value);
      if (code) return Object.assign(new Error(messages[code]), {code, dispatched: false});
    }
  } catch { /* Untrusted provider objects must not expose diagnostics or run accessors. */ }
  return null;
}
