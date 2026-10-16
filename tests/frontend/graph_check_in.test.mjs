import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {deferred, contextWithFunctions, treeDocument, fakeClock, flushPromises} from './monitor_test_helpers.mjs';
const require = createRequire(import.meta.url);
const CheckIn = require('../../src/devgraph/frontend/static/topology/check-in.js');
const item = (id = 'a') => ({kind:'Todo', key:`Todo/${id}`, progress:'not_started', id, title:'Start the plan', status:'draft', priority:'2', version:'1', archived:false});
const page = (items = [item()], extra = {}) => {
  const total=extra.counts?.total ?? items.length;
  return {schema:'devgraph.todos.v2', revision:'r1', complete:true, classification_required:0, items,
    total, next_cursor:extra.has_more ? items.at(-1)?.key : null};
};

test('daily query defaults to all six kinds, explicit progress and independent archival', () => {
  const url = new URL(CheckIn.query({q:'hello & goodbye'}, {key:'Todo/a',revision:'r1'}),'https://localhost');
  assert.equal(url.pathname,'/todos/v2');
  assert.equal(url.searchParams.get('progress'),'not_started');
  assert.equal(url.searchParams.get('archived'),'exclude');
  assert.equal(url.searchParams.get('after'),'Todo/a');
  assert.equal(url.searchParams.has('kind'),false);
  assert.equal(url.searchParams.get('q'),'hello & goodbye');
});
test('all Todo types and parented work are included; only explicit Not started counts', () => {
  for (const kind of ['Todo','Proposal','Initiative','Project','Issue','Task']) {
    assert.equal(CheckIn.decode(page([{...item(),kind,key:`${kind}/a`,parent:'Project/p'}])).items[0].kind,kind);
  }
  for (const change of [{progress:null},{progress:'in_progress'},{progress:'done'},{archived:true}]) {
    assert.throws(()=>CheckIn.decode(page([{...item(),...change}])));
  }
  assert.equal(CheckIn.decode(page([{...item(),priority:'-9223372036854775808'}])).items[0].priority,'-9223372036854775808');
});
test('invalid count, duplicate identity, oversized page and cursor fail closed', () => {
  assert.throws(()=>CheckIn.decode(page([item(),item()])));
  assert.throws(()=>CheckIn.decode(page(Array.from({length:7},(_,i)=>item(String(i))))));
  assert.throws(()=>CheckIn.decode({...page(),next_cursor:'wrong'}));
  assert.throws(()=>CheckIn.decode(page([], {counts:{total:-1}})));
});

function harness() {
  const requests=[];
  const controller=CheckIn.createController({request(path,options) {const task=deferred();requests.push({path,options,...task});return task.promise;}});
  return {controller,requests};
}

test('credential reset aborts and discards old results even if transport ignores abort', async () => {
  const {controller,requests}=harness(); const first=controller.refresh();
  controller.reset(); const next=controller.refresh();
  assert.equal(requests[0].options.signal.aborted,true);
  requests[1].resolve(page([item('new')])); await next;
  requests[0].resolve(page([item('old')])); await first;
  assert.equal(controller.view.data.items[0].id,'new');
});

test('a filter change rejects late unfiltered results and resets pagination', async () => {
  const {controller,requests}=harness(); const first=controller.refresh();
  const filtered=controller.filter({q:'review'});
  requests[1].resolve(page([item('review')])); await filtered;
  requests[0].resolve(page([item('old')])); await first;
  assert.equal(controller.view.data.items[0].id,'review');
  assert.equal(controller.view.page,0);
});

test('pagination uses server cursors, and a failed next page retains the current page', async () => {
  const {controller,requests}=harness(); let pending=controller.refresh();
  requests[0].resolve(page([item('a')],{has_more:true,next_after_id:'a'})); await pending;
  pending=controller.next(); assert.match(requests[1].path,/after=Todo%2Fa/);
  requests[1].reject(new Error('offline')); await pending;
  assert.equal(controller.view.page,0); assert.equal(controller.view.data.items[0].id,'a');
  pending=controller.next(); requests[2].resolve(page([item('b')])); await pending;
  assert.equal(controller.view.page,1);
  pending=controller.previous(); assert.doesNotMatch(requests[3].path,/after=/);
  requests[3].resolve(page([item('a')],{has_more:true,next_after_id:'a'})); await pending;
  assert.equal(controller.view.page,0);
});

test('an old host is unsupported, never broadens into a Draft-based count', async () => {
  const {controller,requests}=harness(); const pending=controller.refresh();
  requests[0].reject(Object.assign(new Error('old host'),{status:501})); await pending;
  assert.equal(requests.length,1); assert.equal(controller.view.status,'unsupported');
  assert.equal(controller.view.data,null);
});

test('authorization and transient Todo failures never trigger fallback', async () => {
  for (const status of [401,403,400,503]) {
    const {controller,requests}=harness(); const pending=controller.refresh();
    requests[0].reject(Object.assign(new Error('failure'),{status})); await pending;
    assert.equal(requests.length,1); assert.equal(controller.view.status,'error');
  }
});

test('a changed list retains rows and retries from the first page', async () => {
  const {controller,requests}=harness(); let pending=controller.refresh();
  requests[0].resolve(page([item()],{has_more:true})); await pending;
  pending=controller.next(); requests[1].reject(Object.assign(new Error('changed'),{status:409})); await pending;
  assert.equal(controller.view.data.items[0].id,'a');
  pending=controller.refresh({force:true}); assert.doesNotMatch(requests[2].path,/after=/);
  requests[2].resolve(page()); await pending; assert.equal(controller.view.page,0);
});

test('revoked access clears previous rows and counts', async () => {
  const {controller,requests}=harness(); let pending=controller.refresh(); requests[0].resolve(page()); await pending;
  pending=controller.refresh(); requests[1].reject(Object.assign(new Error('denied'),{status:403})); await pending;
  assert.equal(controller.view.data,null); assert.match(controller.view.error,/Access denied/);
});

test('Todo reader path cannot accidentally use the five-kind Work API', () => {
  const c=contextWithFunctions(['readPath']);
  assert.equal(c.readPath({category:'todo',kind:'Todo',id:'daily:1'}),'/todos/v2/Todo/daily%3A1');
  assert.equal(c.readPath({category:'work',kind:'Todo',id:'daily:1'}),'/todos/v2/Todo/daily%3A1');
});

function mounted({saved = {}, denyStorage = false} = {}) {
  const doc = treeDocument(), listeners = new Map(), clock = fakeClock();
  doc.addEventListener = (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); };
  doc.removeEventListener = (type, fn) => listeners.get(type)?.delete(fn);
  doc.emit = (type, event) => { for (const fn of listeners.get(type) || []) fn(event); };
  const createElement = doc.createElement;
  doc.createElement = tag => {
    const element = createElement(tag); element.ownerDocument = doc;
    element.value = ''; element.hidden = false;
    let disabled = false;
    Object.defineProperty(element, 'disabled', {get: () => disabled, set(value) {
      disabled = Boolean(value);
      // Browsers blur the active button when it becomes natively disabled.
      if (disabled && doc.activeElement === element) doc.activeElement = doc.body;
    }});
    element.focus = () => { if (!element.disabled) {doc.activeElement = element; doc.emit('focusin', {target: element});} };
    const remove = element.remove.bind(element);
    element.remove = () => { if (element.parentNode && element.contains(doc.activeElement)) doc.activeElement = doc.body; remove(); };
    element.closest = selector => {
      for (let node = element; node; node = node.parentNode) if (selector === '[data-todo-id]' && node.dataset.todoId !== undefined) return node;
      return null;
    };
    element.emit = (type, init = {}) => element.listeners.get(type)?.({type, target: element, preventDefault() {}, ...init});
    return element;
  };
  doc.body = doc.createElement('body'); doc.documentElement = doc.createElement('html'); doc.body.focus();
  const root = doc.createElement('section'), elements = new Map(); doc.body.append(root);
  for (const id of ['todo-items','todo-status','todo-updates','todo-retry','todo-previous','todo-next','todo-count-total','todo-page','todo-kind','todo-search','todo-source']) {
    const node = doc.createElement(id === 'todo-items' ? 'ul' : id === 'todo-search' ? 'input' : /updates|retry|previous|next/.test(id) ? 'button' : 'span');
    node.id = id; elements.set(id, node); root.append(node);
  }
  root.querySelector = selector => elements.get(selector.slice(1));
  const stored = new Map(Object.entries(saved)), writes = [], requests = [], selections = [];
  const context = contextWithFunctions(['reconcileChildren'], {
    document: doc, AbortController, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    localStorage: {
      getItem(key) {if (denyStorage) throw new Error('Denied'); return stored.get(key) ?? null;},
      setItem(key, value) {if (denyStorage) throw new Error('Denied'); stored.set(key, value); writes.push({key, value});},
    },
  });
  vm.runInContext(readFileSync(new URL('../../src/devgraph/frontend/static/topology/check-in.js', import.meta.url), 'utf8'), context);
  const controller = context.DevgraphCheckIn.mount({root, reconcile: context.reconcileChildren,
    request(path, options) {const task = deferred(); requests.push({path, options, ...task}); return task.promise;},
    onSelect(value, options) {selections.push({value, options});},
  });
  const list = elements.get('todo-items');
  return {controller, doc, root, elements, stored, writes, requests, selections, clock, list,
    first: () => list.querySelector('button'),
    async load(value = page()) {const pending = controller.refresh(); requests.at(-1).resolve(value); await pending;},
  };
}

test('unchanged background refresh retains focused Todo DOM and scroll without an update prompt', async () => {
  const f = mounted(); await f.load(); const row = f.first(); row.focus(); f.list.scrollTop = 84;
  await f.load(page([item()], {generated_at:'2026-10-04T12:10:00Z'}));
  assert.equal(f.first(), row); assert.equal(f.doc.activeElement, row); assert.equal(f.list.scrollTop, 84);
  assert.equal(f.elements.get('todo-updates').hidden, true);
  assert.equal(f.root.getAttribute('aria-busy'), 'false');
});

test('focused rows keep their matching counts and selection snapshot until the latest update is accepted', async () => {
  const f = mounted(); await f.load(); const old = f.first(); old.focus(); f.list.scrollTop = 27;
  await f.load(page([item('b'), item('c')]));
  assert.equal(f.first(), old); assert.equal(f.elements.get('todo-count-total').textContent, '1');
  assert.equal(f.elements.get('todo-updates').hidden, false);
  f.list.emit('click', {target: old.querySelector('.todo-item-title')});
  assert.equal(f.selections[0].value.id, 'a');
  await f.load(page([item('latest'),item('latest-2'),item('latest-3')]));
  // Safari pointer activation need not focus a button before dispatching click.
  f.elements.get('todo-updates').emit('click');
  assert.equal(f.first().dataset.todoId, 'Todo/latest');
  assert.equal(f.doc.activeElement, f.first());
  assert.equal(f.elements.get('todo-updates').hidden, true);
  assert.equal(f.elements.get('todo-count-total').textContent, '3');
  f.list.emit('contextmenu', {target: f.first()});
  assert.equal(f.selections.at(-1).value.id, 'latest'); assert.equal(f.selections.at(-1).options.modal, true);
});

test('background changes update counts without moving focus outside the Todo list', async () => {
  const f = mounted(); await f.load(); const search = f.elements.get('todo-search'); search.focus();
  await f.load(page([{...item('draft'),status:'draft'}], {counts:{total:9,review:4,draft:3,accepted:2,archived:0}}));
  assert.equal(f.doc.activeElement, search);
  assert.equal(f.elements.get('todo-count-total').textContent, '9');
  assert.match(f.elements.get('todo-page').textContent, /1 shown/);
});

test('credential reset clears deferred private rows, counts and controls, aborts work, and preserves scoped saves', async () => {
  const key = 'identity-one:todos', saved = JSON.stringify({version:4,q:'private search'});
  const f = mounted({saved:{[key]:saved}}); f.controller.setPreferenceKey(key); await f.load(); f.first().focus();
  await f.load(page([item('deferred')]));
  const pending = f.controller.refresh(); const request = f.requests.at(-1);
  f.elements.get('todo-search').value = 'not yet submitted'; f.elements.get('todo-search').emit('input');
  f.controller.reset();
  assert.equal(request.options.signal.aborted, true); assert.equal(f.first(), null);
  assert.equal(f.elements.get('todo-count-total').textContent, '—');
  assert.equal(f.elements.get('todo-updates').hidden, true);
  assert.equal(f.elements.get('todo-search').value, '');
  assert.equal(f.controller.view.filters.q, ''); assert.equal(f.clock.timers.size, 0);
  request.resolve(page([item('stale')])); await pending;
  assert.equal(f.first(), null); assert.equal(f.stored.get(key), saved);
  f.controller.setPreferenceKey(key);
  assert.equal(f.elements.get('todo-search').value, 'private search');
});

test('revoked access immediately clears a focused deferred list and every count', async () => {
  const f = mounted(); await f.load(); f.first().focus(); await f.load(page([item('new')]));
  const pending = f.controller.refresh(); f.requests.at(-1).reject(Object.assign(new Error('Denied'), {status:403})); await pending;
  assert.equal(f.first(), null); assert.equal(f.elements.get('todo-updates').hidden, true);
  for (const key of ['total']) assert.equal(f.elements.get('todo-count-' + key).textContent, '—');
  assert.match(f.elements.get('todo-status').textContent, /Access denied/);
});

test('scoped saved filters apply before the first request and UI changes persist only that scope', async () => {
  const key = 'identity-one:todos', other = 'graph-preferences';
  const f = mounted({saved:{[key]:JSON.stringify({version:4,q:'retained'}),[other]:'unchanged'}});
  f.controller.setPreferenceKey(key);
  await f.load(); const first = new URL(f.requests[0].path, 'https://localhost');
  assert.equal(first.pathname, '/todos/v2'); assert.equal(first.searchParams.get('q'), 'retained');
  assert.equal(first.searchParams.get('archived'), 'exclude');
  f.elements.get('todo-search').value = 'revised'; f.elements.get('todo-search').emit('input'); f.clock.expireAll();
  assert.equal(f.requests.length, 2); assert.match(f.requests[1].path, /q=revised/);
  assert.deepEqual(JSON.parse(f.stored.get(key)), {version:4,q:'revised',kind:''});
  f.requests[1].resolve(page([])); await flushPromises();
  assert.equal(f.stored.get(other), 'unchanged');
  assert.ok(f.writes.every(write => write.key === key));
  f.requests.at(-1).resolve(page([])); await flushPromises();
});

test('malformed or unavailable preference storage does not prevent filtering and data display', async () => {
  for (const options of [{saved:{prefs:'not json'}}, {denyStorage:true}]) {
    const f = mounted(options); f.controller.setPreferenceKey('prefs');
    assert.equal(f.controller.view.filters.q, ''); assert.equal(f.elements.get('todo-search').value, '');
    f.elements.get('todo-search').value='plan'; f.elements.get('todo-search').emit('input'); f.clock.expireAll();
    assert.match(f.requests[0].path, /q=plan/);
    f.requests[0].resolve(page()); await flushPromises(); assert.equal(f.first().dataset.todoId, 'Todo/a');
  }
});

test('last-page Next and first-page Previous recover lost button focus to the new Todo row', async () => {
  const f = mounted(); await f.load(page([item('a')], {has_more:true,next_after_id:'a'}));
  const next = f.elements.get('todo-next'); next.focus(); const advancing = next.emit('click');
  f.requests.at(-1).resolve(page([item('b')])); await advancing;
  assert.equal(next.disabled, true); assert.equal(f.doc.activeElement, f.first());
  assert.equal(f.first().dataset.todoId, 'Todo/b'); assert.equal(f.elements.get('todo-updates').hidden, true);
  const previous = f.elements.get('todo-previous'); previous.focus(); const returning = previous.emit('click');
  f.requests.at(-1).resolve(page([item('a')], {has_more:true,next_after_id:'a'})); await returning;
  assert.equal(previous.disabled, true); assert.equal(f.doc.activeElement, f.first()); assert.equal(f.first().dataset.todoId, 'Todo/a');
});

test('pagination preserves an enabled trigger and does not reclaim focus moved elsewhere', async () => {
  const f = mounted(); await f.load(page([item('a')], {has_more:true,next_after_id:'a'}));
  const next = f.elements.get('todo-next'); next.focus(); let pending = next.emit('click');
  f.requests.at(-1).resolve(page([item('b')], {has_more:true,next_after_id:'b'})); await pending;
  assert.equal(next.disabled, false); assert.equal(f.doc.activeElement, next);
  pending = next.emit('click'); f.elements.get('todo-search').focus();
  f.requests.at(-1).resolve(page([item('c')])); await pending;
  assert.equal(f.doc.activeElement, f.elements.get('todo-search'));
  assert.equal(f.first().dataset.todoId, 'Todo/c');
});

test('credential reset during pagination prevents later focus recovery or data resurrection', async () => {
  const f = mounted(); await f.load(page([item('a')], {has_more:true,next_after_id:'a'}));
  const next = f.elements.get('todo-next'); next.focus(); const pending = next.emit('click');
  const request = f.requests.at(-1); f.controller.reset(); f.doc.body.focus();
  request.resolve(page([item('stale')])); await pending;
  assert.equal(f.doc.activeElement, f.doc.body); assert.equal(f.first(), null);
});

test('background polling preserves focused pagination and rejects clicks until loading finishes', async () => {
  const f = mounted(); const firstPage = page([item('a')], {has_more:true,next_after_id:'a'});
  await f.load(firstPage); const next = f.elements.get('todo-next'); next.focus();
  let pending = f.controller.refresh();
  assert.ok(f.doc.activeElement === next, 'loading must keep focus on Next');
  assert.equal(next.disabled, false); assert.equal(next.getAttribute('aria-disabled'), 'true');
  await next.emit('click'); assert.equal(f.requests.length, 2, 'guarded loading cannot start a page request');
  f.requests.at(-1).resolve(firstPage); await pending;
  assert.ok(f.doc.activeElement === next); assert.equal(next.getAttribute('aria-disabled'), 'false');
  // Same visible items/counts, but the server no longer has another page.
  pending = f.controller.refresh(); f.requests.at(-1).resolve(page([item('a')])); await pending;
  assert.equal(next.disabled, true); assert.ok(f.doc.activeElement === f.first(), 'an unavailable trigger yields focus to a readable item');
});

test('a refresh returning to the displayed revision clears obsolete deferred updates without rebuilding', async () => {
  const f = mounted(); const original = page(); await f.load(original);
  const button = f.first(); button.focus();
  await f.load(page([item('changed')])); assert.equal(f.elements.get('todo-updates').hidden, false);
  await f.load(original);
  assert.equal(f.elements.get('todo-updates').hidden, true);
  assert.ok(f.first() === button); assert.ok(f.doc.activeElement === button);
  f.elements.get('todo-updates').emit('click');
  assert.ok(f.first() === button); assert.equal(f.first().dataset.todoId, 'Todo/a');
});
