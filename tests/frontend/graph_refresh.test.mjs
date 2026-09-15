import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { contextWithFunctions, deferred, fakeClock, flushPromises, functionsSource, pendingUntilAbort, textDocument } from './monitor_test_helpers.mjs';

function refreshMonitor() {
  const document = textDocument();
  const requests = [], rendered = [];
  const c = contextWithFunctions([
    'synchronizeCredential', 'resetDetailState', 'applySnapshot', 'flushPendingSnapshot', 'refresh',
  ], {
    AbortController, queueMicrotask, document,
    tokenInput: { value: 'synthetic-credential-one' },
    state: {
      snapshot: null, observations: [], selectedGraphKey: null, authEpoch: 0,
      refreshPromise: null, refreshQueued: false, pendingSnapshot: null, lastSuccess: null,
    },
    detailState: {
      credential: 'synthetic-credential-one', serial: 0, node: null, data: null,
      relations: new Map(), documents: new Map(), controller: null,
    },
    graphView: { pointers: new Map(), nodePositions: new Map(), nodeVelocities: new Map() },
    text(id, value) { document.getElementById(id).textContent = value; },
    render(snapshot, observations) { rendered.push({ snapshot, observations }); },
    updateGraphVisibility() {}, loadSelectedDetail() {},
    renderActivity() {}, renderObservations() {}, renderPipeline() {}, renderBars() {},
    getJson(path, options) { const task = deferred(); requests.push({ path, options, ...task }); return task.promise; },
  });
  return { c, requests, rendered };
}

function resolveRefresh(requests, index, snapshot) {
  requests[index].resolve(snapshot);
  requests[index + 1].resolve({ items: [] });
}

function boundedTransport(c, fetch) {
  const clock = fakeClock();
  Object.assign(c, {
    AbortController, DOMException, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    window: clock, headers: () => ({ Authorization: `Bearer ${c.tokenInput.value}` }), fetch,
  });
  vm.runInContext(functionsSource(['getJson']), c);
  return clock;
}

test('snapshot refresh allows one flight and coalesces multiple requests into one queued update', async () => {
  const { c, requests, rendered } = refreshMonitor();
  const initial = c.refresh();
  const duplicate = c.refresh();
  const duplicateAgain = c.refresh();
  assert.equal(requests.length, 2, 'one snapshot and one observations read');
  const first = { generated_at: '2026-09-13T01:00:00Z', graph_nodes: [{ id: 'before' }] };
  resolveRefresh(requests, 0, first);
  await Promise.all([initial, duplicate, duplicateAgain]); await flushPromises();
  assert.equal(requests.length, 4, 'all duplicate triggers produce exactly one subsequent refresh');
  assert.equal(c.state.snapshot, first, 'last successful snapshot remains readable while the next request runs');
  const current = { generated_at: '2026-09-13T01:00:01Z', graph_nodes: [{ id: 'after' }] };
  const queued = c.state.refreshPromise;
  resolveRefresh(requests, 2, current); await queued; await flushPromises();
  assert.equal(c.state.snapshot, current);
  assert.equal(rendered.length, 2);
  assert.equal(requests.length, 4);
  assert.equal(c.state.refreshPromise, null);
});

test('old-auth responses cannot overwrite a newer credential’s snapshot or clear its in-flight request', async () => {
  const { c, requests } = refreshMonitor();
  const old = c.refresh();
  c.tokenInput.value = 'synthetic-credential-two';
  const current = c.refresh();
  const currentFlight = c.state.refreshPromise;
  const stale = { generated_at: '2026-09-13T01:00:00Z', graph_nodes: [{ id: 'private-old' }] };
  resolveRefresh(requests, 0, stale); await old;
  assert.equal(c.state.snapshot, null);
  assert.equal(c.state.refreshPromise, currentFlight);
  const latest = { generated_at: '2026-09-13T01:00:01Z', graph_nodes: [{ id: 'private-new' }] };
  resolveRefresh(requests, 2, latest); await current;
  assert.equal(c.state.snapshot, latest);
  assert.equal(c.state.refreshPromise, null);
});

test('changing or clearing the credential removes prior graph, reader, and document content immediately', () => {
  const { c } = refreshMonitor();
  c.state.snapshot = { graph_nodes: [{ id: 'private-work' }] };
  c.state.selectedGraphKey = 'private-work';
  c.state.pendingSnapshot = { snapshot: { graph_nodes: [{ id: 'queued-private' }] } };
  c.detailState.data = { description: 'Private plan' };
  c.detailState.documents.set('plan', { content: 'Private document' });
  c.detailState.relations.set('children', { items: [{ id: 'private-child' }] });
  c.graphView.nodePositions.set('private-work', { x: 0 });
  c.graphView.arenaKey = 'Arena:private';
  c.tokenInput.value = '';
  c.synchronizeCredential();
  assert.equal(c.state.snapshot, null);
  assert.equal(c.state.pendingSnapshot, null);
  assert.equal(c.state.selectedGraphKey, null);
  assert.equal(c.detailState.data, null);
  assert.equal(c.detailState.documents.size, 0);
  assert.equal(c.detailState.relations.size, 0);
  assert.equal(c.graphView.nodePositions.size, 0);
  assert.equal(c.graphView.arenaKey, '');
});

test('a failed refresh retains the latest successful snapshot and reports staleness rather than empty data', async () => {
  const { c, requests, rendered } = refreshMonitor();
  const first = { generated_at: '2026-09-13T01:00:00Z', graph_nodes: [{ id: 'stored-work' }] };
  const loaded = c.refresh(); resolveRefresh(requests, 0, first); await loaded;
  const failed = c.refresh();
  requests[2].reject(Object.assign(new Error('offline'), { status: 503 }));
  requests[3].resolve({ items: [] }); await failed;
  assert.equal(c.state.snapshot, first);
  assert.equal(rendered.length, 1);
  assert.match(c.document.getElementById('status-line').textContent, /Showing saved snapshot/);
  assert.equal(c.document.getElementById('connection-label').textContent, 'Connection interrupted');
});

test('manipulation defers snapshot changes and releases only the latest queued projection', () => {
  const { c, rendered } = refreshMonitor();
  const before = { graph_nodes: [{ id: 'dragging-node' }] };
  const intermediate = { graph_nodes: [] };
  const latest = { graph_nodes: [{ id: 'latest-node' }] };
  c.state.snapshot = before;
  c.graphView.pointers.set(1, { x: 30, y: 60 });
  c.applySnapshot(intermediate, []);
  c.applySnapshot(latest, []);
  c.flushPendingSnapshot();
  assert.equal(c.state.snapshot, before);
  assert.equal(rendered.length, 0);
  c.graphView.pointers.clear(); c.state.controlDragging = true;
  c.flushPendingSnapshot();
  assert.equal(c.state.snapshot, before, 'a control drag also retains the working projection');
  c.state.controlDragging = false;
  c.flushPendingSnapshot();
  assert.equal(c.state.snapshot, latest);
  assert.equal(c.state.pendingSnapshot, null);
  assert.equal(rendered.length, 1);
});

test('a deferred refresh does not claim that the displayed snapshot has already updated', async () => {
  const { c, requests } = refreshMonitor();
  const first = { generated_at: '2026-09-13T01:00:00Z', graph_nodes: [{ id: 'still-displayed' }] };
  const loaded = c.refresh(); resolveRefresh(requests, 0, first); await loaded;
  c.graphView.pointers.set(1, { x: 10, y: 20 });
  const waiting = c.refresh();
  const deferredSnapshot = { generated_at: '2026-09-13T02:00:00Z', graph_nodes: [{ id: 'not-shown-yet' }] };
  resolveRefresh(requests, 2, deferredSnapshot); await waiting;
  const failed = c.refresh();
  requests[4].reject(new Error('offline')); requests[5].resolve({ items: [] }); await failed;
  assert.equal(c.state.snapshot, first);
  const status = c.document.getElementById('status-line').textContent;
  assert.ok(status.includes(new Date(first.generated_at).toLocaleTimeString()), 'saved-snapshot time must describe the graph currently on screen');
  assert.ok(!status.includes(new Date(deferredSnapshot.generated_at).toLocaleTimeString()), 'a pending projection must not be advertised as displayed');
});

test('releasing a drag after a newer refresh failure cannot apply an older deferred success', async () => {
  const { c, requests, rendered } = refreshMonitor();
  const first = { generated_at: '2026-09-13T01:00:00Z', graph_nodes: [{ id: 'displayed' }] };
  const loaded = c.refresh(); resolveRefresh(requests, 0, first); await loaded;
  c.graphView.pointers.set(1, { x: 10, y: 20 });
  const waiting = c.refresh();
  const deferredSnapshot = { generated_at: '2026-09-13T02:00:00Z', graph_nodes: [{ id: 'deferred' }] };
  resolveRefresh(requests, 2, deferredSnapshot); await waiting;
  const failed = c.refresh();
  requests[4].reject(Object.assign(new Error('audit unavailable'), { status: 503 }));
  requests[5].resolve({ items: [] }); await failed;
  assert.equal(c.state.pendingSnapshot, null, 'a failure supersedes any older queued success');
  c.graphView.pointers.clear();
  c.flushPendingSnapshot();
  assert.equal(c.state.snapshot, first);
  assert.equal(rendered.length, 1, 'render must not restore a Graph ready status after failure');
  assert.equal(c.document.getElementById('connection-label').textContent, 'Connection interrupted');
  const recovered = c.refresh();
  const latest = { generated_at: '2026-09-13T03:00:00Z', graph_nodes: [{ id: 'recovered' }] };
  resolveRefresh(requests, 6, latest); await recovered;
  assert.equal(c.state.snapshot, latest, 'a later successful read still updates normally');
});

test('a timed-out snapshot request releases the refresh flight so the next update can succeed', async () => {
  const { c } = refreshMonitor();
  const clock = boundedTransport(c, (_url, { signal }) => pendingUntilAbort(signal));
  const stalled = c.refresh();
  assert.equal(clock.timers.size, 2, 'each bounded API read has its own deadline');
  clock.expireAll(); await stalled;
  assert.equal(c.state.refreshPromise, null);
  assert.equal(clock.timers.size, 0);
  assert.equal(c.document.getElementById('connection-label').textContent, 'Connection interrupted');
  const fresh = { generated_at: '2026-09-13T03:00:00Z', graph_nodes: [{ id: 'recovered' }] };
  c.fetch = async url => ({ ok: true, status: 200, json: async () => url === '/monitor/snapshot' ? fresh : { items: [] } });
  await c.refresh();
  assert.equal(c.state.snapshot, fresh);
  assert.equal(c.state.refreshPromise, null);
  assert.equal(clock.timers.size, 0);
});

test('a failed refresh member aborts its sibling and drops queued immediate retries', async () => {
  for (const failingPath of ['/monitor/snapshot', '/initiative-observations?descending=true&limit=100']) {
    const { c } = refreshMonitor();
    const failed = deferred(), transport = [];
    const clock = boundedTransport(c, (path, { signal }) => {
      transport.push({ path, signal });
      return path === failingPath ? failed.promise : pendingUntilAbort(signal);
    });
    const initial = c.refresh(), queued = c.refresh();
    assert.equal(c.state.refreshQueued, true);
    failed.reject(new Error('Service unavailable'));
    await Promise.all([initial, queued]); await flushPromises();
    assert.equal(transport.find(request => request.path !== failingPath).signal.aborted, true, 'the paired read must not hang until its own deadline');
    assert.equal(transport.length, 2, 'failure must wait for the next normal refresh trigger');
    assert.equal(clock.timers.size, 0);
    assert.equal(c.state.refreshQueued, false);
    assert.equal(c.state.refreshPromise, null);
    assert.equal(c.state.refreshController, null);
  }
});

test('changing credentials aborts both old network reads while allowing the new authenticated pair to complete', async () => {
  const { c } = refreshMonitor();
  const transport = [];
  const snapshot = { generated_at: '2026-09-13T04:00:00Z', graph_nodes: [{ id: 'new-auth-work' }] };
  const clock = boundedTransport(c, (path, { signal, headers }) => {
    transport.push({ path, signal, headers });
    if (transport.length <= 2) return pendingUntilAbort(signal);
    return Promise.resolve({ ok: true, status: 200, json: async () => path === '/monitor/snapshot' ? snapshot : { items: [] } });
  });
  const old = c.refresh();
  c.tokenInput.value = 'synthetic-credential-two';
  const current = c.refresh();
  assert.equal(transport[0].signal.aborted, true);
  assert.equal(transport[1].signal.aborted, true);
  assert.equal(transport[2].signal.aborted, false);
  assert.equal(transport[3].signal.aborted, false);
  await Promise.all([old, current]);
  assert.equal(c.state.snapshot, snapshot);
  assert.equal(transport[2].headers.Authorization, 'Bearer synthetic-credential-two');
  assert.equal(clock.timers.size, 0);
  assert.equal(c.state.refreshPromise, null);
  assert.equal(c.state.refreshController, null);
});
