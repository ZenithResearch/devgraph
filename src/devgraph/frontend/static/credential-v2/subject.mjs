const hex = bytes => [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
const error = message => Object.assign(Error(message), {dispatched: false});
/** Normalize a public Ed25519 subject; never derive a new identity. */
export function subjectPublicKey(subject) {
  const value = subject?.publicKey;
  if (typeof value !== 'string') throw error('The wallet identity is unavailable.');
  if (/^[0-9a-f]{64}$/.test(value)) return value;
  const match = /^-----BEGIN PUBLIC KEY-----\r?\n([A-Za-z0-9+/=\r\n]+)\r?\n-----END PUBLIC KEY-----\r?\n?$/.exec(value);
  if (!match) throw error('The wallet identity is unavailable.');
  const base64 = match[1].replace(/[\r\n]/g, '');
  if (!/^[A-Za-z0-9+/]{58}[AEIMQUYcgkosw048]=$/.test(base64))
    throw error('The wallet identity is unavailable.');
  let bytes;
  try {bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));}
  catch {throw error('The wallet identity is unavailable.');}
  if (bytes.length !== 44 || hex(bytes.slice(0, 12)) !== '302a300506032b6570032100'
    || btoa(String.fromCharCode(...bytes)) !== base64) throw error('The wallet identity is unavailable.');
  return hex(bytes.slice(12));
}
