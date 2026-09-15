import {runSelection} from '../core/index.mjs';
self.onmessage = async ({data}) => {
  try { self.postMessage({ok: true, result: await runSelection(data)}); }
  catch (e) { self.postMessage({ok: false, error: {code: e.code || 'run_failed', message: e.message, details: e.details || []}}); }
};
