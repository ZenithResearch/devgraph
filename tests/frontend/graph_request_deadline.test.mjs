import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions, fakeClock, flushPromises, pendingUntilAbort } from './monitor_test_helpers.mjs';

function requestContext() {
  const clock = fakeClock();
  const c = contextWithFunctions(['getJson'], {
    AbortController, DOMException, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    window: clock,
    headers() { return { Authorization: 'Bearer synthetic-deadline-test' }; },
  });
  return { c, clock };
}

function trackedController() {
  const controller = new AbortController();
  const listeners = new Set();
  const add = controller.signal.addEventListener.bind(controller.signal);
  const remove = controller.signal.removeEventListener.bind(controller.signal);
  controller.signal.addEventListener = (type, callback, options) => { if (type === 'abort') listeners.add(callback); add(type, callback, options); };
  controller.signal.removeEventListener = (type, callback, options) => { if (type === 'abort') listeners.delete(callback); remove(type, callback, options); };
  return { controller, listeners };
}

test('an unresponsive fetch is aborted at the deadline and reported as a timeout', async () => {
  const { c, clock } = requestContext();
  let requestSignal;
  c.fetch = (_url, { signal }) => { requestSignal = signal; return pendingUntilAbort(signal); };
  const pending = c.getJson('/monitor/snapshot');
  const rejected = assert.rejects(pending, error => error.name === 'TimeoutError');
  assert.equal(clock.timers.size, 1);
  assert.equal([...clock.timers.values()][0].milliseconds, 30000);
  clock.expireAll(); await rejected;
  assert.equal(requestSignal.aborted, true);
  assert.equal(clock.timers.size, 0);
});

test('the deadline covers a stalled JSON body and cannot become an empty successful payload', async () => {
  const { c, clock } = requestContext();
  c.fetch = async (_url, { signal }) => ({ ok: true, status: 200, json: () => pendingUntilAbort(signal) });
  const pending = c.getJson('/work/Task/slow-body');
  const rejected = assert.rejects(pending, error => error.name === 'TimeoutError');
  await flushPromises();
  clock.expireAll(); await rejected;
  assert.equal(clock.timers.size, 0);
});

test('a superseding selection forwards caller cancellation and removes its listener and timer', async () => {
  const { c, clock } = requestContext();
  const { controller, listeners } = trackedController();
  let requestSignal;
  c.fetch = (_url, { signal }) => { requestSignal = signal; return pendingUntilAbort(signal); };
  const pending = c.getJson('/work/Task/old-selection', { signal: controller.signal });
  const rejected = assert.rejects(pending, error => error.name === 'AbortError');
  assert.notEqual(requestSignal, controller.signal, 'deadline uses its own composed cancellation signal');
  assert.equal(listeners.size, 1);
  controller.abort(); await rejected;
  assert.equal(requestSignal.aborted, true);
  assert.equal(clock.timers.size, 0);
  assert.equal(listeners.size, 0);
});

test('already-aborted caller signals cannot start a successful read', async () => {
  const { c, clock } = requestContext();
  const { controller, listeners } = trackedController();
  controller.abort();
  c.fetch = (_url, { signal }) => pendingUntilAbort(signal);
  await assert.rejects(c.getJson('/work/Task/obsolete', { signal: controller.signal }), error => error.name === 'AbortError');
  assert.equal(clock.timers.size, 0);
  assert.equal(listeners.size, 0);
});

test('successful and HTTP-error reads release deadline resources', async () => {
  for (const status of [200, 403]) {
    const { c, clock } = requestContext();
    const { controller, listeners } = trackedController();
    c.fetch = async () => ({ ok: status === 200, status, json: async () => status === 200 ? { title: 'Loaded' } : { title: 'Read denied' } });
    const pending = c.getJson('/work/Task/a', { signal: controller.signal });
    if (status === 200) assert.equal((await pending).title, 'Loaded');
    else await assert.rejects(pending, error => error.status === 403);
    assert.equal(clock.timers.size, 0);
    assert.equal(listeners.size, 0);
  }
});
