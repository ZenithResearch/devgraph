import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions, deferred, flushPromises, textDocument } from './monitor_test_helpers.mjs';

const first = { key: 'Task:a', category: 'work', kind: 'Task', id: 'a', title: 'First task' };
const second = { key: 'Task:b', category: 'work', kind: 'Task', id: 'b', title: 'Second task' };

test('Arena details use their independent read route', () => {
  const c = contextWithFunctions(['readPath']);
  assert.equal(c.readPath({category: 'arena', kind: 'Arena', id: 'gallery'}), '/arenas/gallery');
  assert.equal(c.readPath({category: 'work', kind: 'Arena', id: 'gallery'}), null);
});

function reader() {
  const document = textDocument();
  document.getElementById('graph-details').classList = { remove() {} };
  const openRelationships = [];
  document.getElementById('reader-content').querySelectorAll = () => openRelationships.map(relationship => ({ dataset: { relationship } }));
  const requests = [];
  const c = contextWithFunctions([
    'safeSourceUrl', 'appendSource', 'readPath', 'resetDetailState', 'selectGraphNode',
    'loadSelectedDetail', 'refreshSelectedResources', 'loadRelationship', 'loadSupporting', 'loadDocument', 'getJson',
  ], {
    URL, AbortController, setTimeout, clearTimeout,
    window: { setTimeout, clearTimeout },
    state: { selectedGraphKey: null, snapshot: null, authEpoch: 0 },
    detailState: {
      node: null, data: null, error: null, loading: false, serial: 0,
      controller: null, promise: null, relations: new Map(), support: null,
      supportLoading: false, supportError: null, documents: new Map(), rendered: null,
    },
    graphView: { nodes: [first, second], edges: [] },
    document, pauseGraphOrbit() {}, renderGraph() {}, renderGraphSelection() {},
    text(id, value) { document.getElementById(id).textContent = value; },
    make(tag, className, textContent) { return { tag, className, textContent }; },
  });
  const realGetJson = c.getJson;
  c.getJson = (path, options = {}) => {
    const response = deferred(); requests.push({ path, options, ...response });
    return response.promise;
  };
  return { c, requests, realGetJson, openRelationships };
}

async function loadedReader() {
  const result = reader();
  result.c.selectGraphNode(first);
  const promise = result.c.detailState.promise;
  result.requests[0].resolve({ id: 'a', title: 'First task', description: 'A complete\nmultiline plan.', version: 4 });
  await promise;
  return result;
}

test('rapid A → B selection rejects late A data even when abort is ignored by the network', async () => {
  const { c, requests } = reader();
  c.selectGraphNode(first);
  const oldPromise = c.detailState.promise;
  c.selectGraphNode(second);
  const currentPromise = c.detailState.promise;
  assert.equal(requests[0].options.signal.aborted, true);
  requests[1].resolve({ id: 'b', description: 'The current plan', version: 2 });
  await currentPromise;
  requests[0].resolve({ id: 'a', description: 'Obsolete result', version: 1 });
  await oldPromise;
  assert.equal(c.state.selectedGraphKey, 'Task:b');
  assert.equal(c.detailState.node.id, 'b');
  assert.equal(c.detailState.data.description, 'The current plan');
  assert.equal(c.detailState.error, null);
  assert.equal(c.detailState.promise, null);
});

test('a late failed request cannot replace the selected record’s success state', async () => {
  const { c, requests } = reader();
  c.selectGraphNode(first); const oldPromise = c.detailState.promise;
  c.selectGraphNode(second); const currentPromise = c.detailState.promise;
  requests[1].resolve({ id: 'b', title: 'Current record', version: 3 });
  await currentPromise;
  requests[0].reject(Object.assign(new Error('gone'), { status: 404 }));
  await oldPromise;
  assert.equal(c.detailState.data.title, 'Current record');
  assert.equal(c.detailState.error, null);
});

test('detail refresh is single-flight and retains readable data while a request is pending', async () => {
  const { c, requests } = await loadedReader();
  const data = c.detailState.data;
  const firstRefresh = c.loadSelectedDetail();
  const secondRefresh = c.loadSelectedDetail();
  assert.equal(requests.length, 2, 'one initial read plus one refresh, with no duplicate read');
  assert.equal(c.detailState.data, data);
  assert.equal(c.detailState.loading, false, 'refresh must not replace readable content with initial loading state');
  requests[1].resolve({ ...data });
  await Promise.all([firstRefresh, secondRefresh]);
  assert.equal(c.detailState.data.description, 'A complete\nmultiline plan.');
});

test('refreshing an unchanged record retains related work, documents, and reading position', async () => {
  const { c, requests } = await loadedReader();
  const related = { items: [{ id: 'child' }], loaded: true };
  const document = { content: 'Authored plan text' };
  c.detailState.relations.set('children', related);
  c.detailState.documents.set('plan', document);
  c.document.getElementById('reader-body').scrollTop = 274;
  const serial = c.detailState.serial;
  c.selectGraphNode({ ...first });
  const promise = c.detailState.promise;
  requests[1].resolve({ id: 'a', description: 'A complete\nmultiline plan.', version: 4 });
  await promise;
  assert.equal(c.detailState.serial, serial);
  assert.equal(c.document.getElementById('reader-body').scrollTop, 274);
  assert.equal(c.detailState.relations.get('children'), related);
  assert.equal(c.detailState.documents.get('plan'), document);
});

test('changed Work versions mark old supporting content stale and preserve it until replacement loads', async () => {
  const { c, requests } = await loadedReader();
  c.detailState.relations.set('children', { loaded: true, items: [{ id: 'old-child' }] });
  c.detailState.documents.set('old-plan', { content: 'Old linked plan' });
  c.detailState.support = { items: [{ id: 'old-plan' }] };
  const promise = c.loadSelectedDetail();
  requests[1].resolve({ id: 'a', description: 'A revised plan.', version: 5 });
  await promise;
  assert.equal(c.detailState.data.version, 5);
  assert.equal(c.detailState.relations.get('children').stale, true);
  assert.equal(c.detailState.documents.get('old-plan').content, 'Old linked plan');
  assert.equal(c.detailState.support.items[0].id, 'old-plan');
  assert.equal(c.detailState.supportStale, true);
  requests[2].resolve({ items: [], next_cursor: null }); await flushPromises();
  assert.equal(c.detailState.documents.size, 0, 'removed attachment content disappears after the parent’s new attachment list loads');
  assert.equal(c.detailState.support.items.length, 0);
  assert.equal(c.detailState.supportStale, false);
});

test('old supporting responses cannot repopulate caches after the parent Work version changes', async () => {
  const { c, requests } = await loadedReader();
  const supporting = c.loadSupporting();
  const document = c.loadDocument('old-plan');
  const related = c.loadRelationship('children');
  const refreshed = c.loadSelectedDetail();
  requests[4].resolve({ id: 'a', description: 'A new plan with changed attachments', version: 5 });
  await refreshed;
  requests[5].resolve({ items: [], next_cursor: null }); await flushPromises();
  requests[1].resolve({ items: [{ id: 'old-plan', kind: 'Artifact' }] });
  requests[2].resolve({ content: 'Plan from version 4' });
  requests[3].resolve({ items: [{ id: 'old-child', kind: 'Task' }] });
  await Promise.all([supporting, document, related]);
  assert.equal(c.detailState.data.version, 5);
  assert.equal(c.detailState.support.items.length, 0);
  assert.equal(c.detailState.documents.size, 0);
  assert.equal(c.detailState.relations.get('children').items.length, 0);
  assert.equal(c.detailState.supportLoading, false);
});

test('a changed authentication epoch discards in-flight private content and clears caches', async () => {
  const { c, requests } = await loadedReader();
  c.detailState.documents.set('plan', { content: 'Private authored text' });
  c.detailState.relations.set('children', { items: [] });
  const pending = c.loadSelectedDetail();
  c.state.authEpoch += 1;
  c.resetDetailState();
  requests[1].resolve({ id: 'a', description: 'Stale private content', version: 4 });
  await pending;
  assert.equal(c.detailState.node, null);
  assert.equal(c.detailState.data, null);
  assert.equal(c.detailState.documents.size, 0);
  assert.equal(c.detailState.relations.size, 0);
});

test('read failures distinguish missing, denied, and unavailable records while retaining prior content', async () => {
  for (const [status, message] of [[404, /no longer available/], [403, /denied/], [500, /Could not update/]]) {
    const { c, requests } = await loadedReader();
    const old = c.detailState.data;
    const pending = c.loadSelectedDetail();
    requests[1].reject(Object.assign(new Error('fixture failure'), { status }));
    await pending;
    assert.match(c.detailState.error, message);
    assert.equal(c.detailState.data, old);
    assert.equal(c.detailState.loading, false);
  }
});

test('category-specific reads encode IDs and never send observations or receipts to Work endpoints', () => {
  const { c } = reader();
  assert.equal(c.readPath({ ...first, id: 'path/with ? punctuation' }), '/work/Task/path%2Fwith%20%3F%20punctuation');
  assert.equal(c.readPath({ category: 'observation', kind: 'InitiativeObservation', id: 'obs/1' }), '/initiative-observations/obs%2F1');
  assert.equal(c.readPath({ category: 'receipt', kind: 'EventReceipt', id: 'r' }), null);
  assert.equal(c.readPath({ category: 'work', kind: 'Artifact', id: 'a' }), null);
});

test('relationship paging uses the last typed identity and deduplicates repeated boundaries', async () => {
  const { c, requests } = await loadedReader();
  const items = Array.from({ length: 50 }, (_, id) => ({ kind: 'Task', id: String(id), title: `Child ${id}` }));
  const initial = c.loadRelationship('children');
  c.loadRelationship('children');
  assert.equal(requests.length, 2, 'opening an already-loading section must not duplicate the request');
  requests[1].resolve({ items }); await initial;
  assert.equal(c.detailState.relations.get('children').more, true);
  const next = c.loadRelationship('children', true);
  assert.match(requests[2].path, /after_resource=Task%2F49$/);
  const wireCursor = new URL(requests[2].path, 'http://127.0.0.1').searchParams.get('after_resource');
  assert.equal(wireCursor, 'Task/49', 'the relationship API accepts Kind/id resource identities, not graph-key Kind:id syntax');
  requests[2].resolve({ items: [items.at(-1), { kind: 'Task', id: '50', title: 'Last child' }] }); await next;
  assert.equal(c.detailState.relations.get('children').items.length, 51);
  assert.equal(c.detailState.relations.get('children').more, false);
});

test('open relationships revalidate child titles even when the selected parent version is unchanged', async () => {
  const { c, requests, openRelationships } = await loadedReader();
  openRelationships.push('children');
  const initial = c.loadRelationship('children');
  requests[1].resolve({ items: [{ kind: 'Task', id: 'child', title: 'Old child title', status: 'draft' }] }); await initial;
  const refresh = c.loadSelectedDetail();
  requests[2].resolve({ id: 'a', title: 'First task', version: 4 }); await refresh;
  assert.match(requests[3].path, /\/work\/Task\/a\/relationships\/children\?limit=50$/);
  assert.equal(c.detailState.relations.get('children').items[0].title, 'Old child title', 'readable child content remains during revalidation');
  requests[3].resolve({ items: [{ kind: 'Task', id: 'child', title: 'Revised child title', status: 'accepted' }] }); await flushPromises();
  assert.equal(c.detailState.relations.get('children').items[0].title, 'Revised child title');
  assert.equal(c.detailState.relations.get('children').items[0].status, 'accepted');
});

test('an open relationship section loads the newly selected Work without requiring another toggle', async () => {
  const { c, requests, openRelationships } = await loadedReader();
  openRelationships.push('children');
  c.detailState.relations.set('children', { loaded: true, items: [{ kind: 'Task', id: 'old-child' }] });
  c.selectGraphNode(second); const pending = c.detailState.promise;
  requests[1].resolve({ id: 'b', title: 'Second task', version: 1 }); await pending;
  assert.match(requests[2].path, /\/work\/Task\/b\/relationships\/children\?limit=50$/);
  requests[2].resolve({ items: [{ kind: 'Task', id: 'new-child' }] }); await flushPromises();
  assert.deepEqual(Array.from(c.detailState.relations.get('children').items, item => item.id), ['new-child']);
});

test('relationship refresh preserves the loaded range until every replacement page completes', async () => {
  const { c, requests } = await loadedReader();
  const oldItems = Array.from({ length: 51 }, (_, index) => ({ kind: 'Task', id: `child-${index}`, title: 'Old title' }));
  c.detailState.relations.set('children', { loaded: true, more: true, items: oldItems });
  const refreshed = c.loadRelationship('children', false, true);
  const fresh = oldItems.map(item => ({ ...item, title: 'New title' }));
  requests[1].resolve({ items: fresh.slice(0, 50) }); await flushPromises();
  assert.equal(c.detailState.relations.get('children').items, oldItems);
  assert.match(requests[2].path, /after_resource=Task%2Fchild-49$/);
  requests[2].resolve({ items: fresh.slice(50) }); await refreshed;
  assert.equal(c.detailState.relations.get('children').items.length, 51);
  assert.equal(c.detailState.relations.get('children').items.at(-1).title, 'New title');
});

test('supporting-material refresh preserves previously loaded pages until replacement is complete', async () => {
  const { c, requests } = await loadedReader();
  const oldItems = Array.from({ length: 51 }, (_, index) => ({ kind: 'Artifact', id: `plan-${index}`, resolution: 'available' }));
  c.detailState.support = { items: oldItems, next_cursor: 'old-cursor' };
  const refreshed = c.loadSupporting(false, true);
  requests[1].resolve({ items: oldItems.slice(0, 50), next_cursor: 'fresh-cursor' }); await flushPromises();
  assert.equal(c.detailState.support.items, oldItems);
  assert.match(requests[2].path, /after=fresh-cursor$/);
  requests[2].resolve({ items: oldItems.slice(50), next_cursor: null }); await refreshed;
  assert.equal(c.detailState.support.items.length, 51);
  assert.equal(c.detailState.support.next_cursor, null);
});

test('document revalidation retains readable text on network errors and removes it when detached', async () => {
  const { c, requests } = await loadedReader();
  c.detailState.documents.set('plan', { content: 'Long authored plan' });
  const refreshed = c.loadDocument('plan', true);
  assert.equal(c.detailState.documents.get('plan').content, 'Long authored plan');
  assert.equal(c.detailState.documents.get('plan').loading, false);
  assert.equal(c.detailState.documents.get('plan').refreshing, true);
  requests[1].reject(Object.assign(new Error('offline'), { status: 503 })); await refreshed;
  assert.equal(c.detailState.documents.get('plan').content, 'Long authored plan');
  assert.equal(c.detailState.documents.get('plan').stale, true);
  assert.match(c.detailState.documents.get('plan').refreshError, /last loaded text/);
  const missing = c.loadDocument('plan', true);
  requests[2].reject(Object.assign(new Error('detached'), { status: 404 })); await missing;
  assert.equal(c.detailState.documents.get('plan').content, undefined);
  assert.equal(c.detailState.documents.get('plan').state, 'missing');
});

test('late attachment and document responses cannot enter a newly selected reader', async () => {
  const { c, requests } = await loadedReader();
  const support = c.loadSupporting();
  const document = c.loadDocument('old-plan');
  c.selectGraphNode(second); const current = c.detailState.promise;
  requests[3].resolve({ id: 'b', title: 'New selection', version: 1 }); await current;
  requests[1].resolve({ items: [{ kind: 'Artifact', id: 'old-plan' }], next_cursor: null });
  requests[2].resolve({ content: 'Old selection’s document' });
  await Promise.all([support, document]);
  assert.equal(c.detailState.support, null);
  assert.equal(c.detailState.documents.size, 0);
  assert.equal(c.detailState.data.id, 'b');
});

test('external source links permit only explicit HTTP(S) URLs without embedded credentials', () => {
  const { c } = reader();
  for (const unsafe of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///etc/passwd', '//example.com/plan', '/relative', 'https://user:password@example.com/plan', 'not a URL']) {
    assert.equal(c.safeSourceUrl(unsafe), null, unsafe);
  }
  const appended = [];
  assert.equal(c.appendSource({ append(link) { appended.push(link); } }, 'https://example.com/plan?a=1#section'), true);
  assert.equal(appended[0].href, 'https://example.com/plan?a=1#section');
  assert.equal(appended[0].target, '_blank');
  assert.equal(appended[0].rel, 'noopener noreferrer');
  assert.equal(c.appendSource({ append() { assert.fail('unsafe link appended'); } }, 'javascript:alert(1)'), false);
});

test('authenticated JSON reads reject redirects and preserve response status for accurate error states', async () => {
  const { c, realGetJson } = reader();
  c.headers = () => ({ Authorization: 'Bearer synthetic-test-credential' });
  let options;
  c.fetch = async (_path, value) => { options = value; return { ok: false, status: 403, json: async () => ({ title: 'Read denied' }) }; };
  await assert.rejects(realGetJson('/work/Task/a'), error => error.status === 403 && error.message === 'Read denied');
  assert.equal(options.redirect, 'error', 'bearer-bearing requests must not follow source redirects');
});
