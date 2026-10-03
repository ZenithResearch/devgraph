import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../src/devgraph/frontend/static/topology/theme.js', import.meta.url), 'utf8');
const key = 'devgraph.theme.v1';

function eventTarget() {
  const handlers = new Map();
  return {
    addEventListener(type, fn, options = {}) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type).push({fn, once: options.once});
    },
    dispatchEvent(event) {
      for (const handler of [...handlers.get(event.type) || []]) {
        handler.fn(event);
        if (handler.once) handlers.set(event.type, handlers.get(event.type).filter(item => item !== handler));
      }
    },
  };
}

function browser(options = {}) {
  const writes = [], transitions = [], pickers = [], statuses = [], dataset = {};
  const stored = new Map(options.saved === undefined ? [] : [[key, options.saved]]);
  const storage = {
    getItem(name) { if (options.denyRead) throw new Error('Denied'); return stored.get(name) ?? null; },
    setItem(name, value) {
      if (options.denyWrite) throw new Error('Denied');
      stored.set(name, value); writes.push([name, value]);
    },
  };
  const doc = Object.assign(eventTarget(), {
    documentElement: {dataset}, readyState: options.readyState || 'loading', activeElement: {id: 'retain-focus'},
    querySelectorAll(selector) {
      if (selector === 'select[data-theme-picker]') return pickers;
      if (selector === '[data-theme-status]') return statuses;
      throw new Error(`Unexpected query ${selector}`);
    },
  });
  const media = Object.assign(eventTarget(), {matches: options.dark ?? false});
  if (options.legacyMedia) {
    media.addListener = fn => { media.legacyListener = fn; };
    delete media.addEventListener;
  }
  const global = Object.assign(eventTarget(), {
    document: doc,
    matchMedia(query) {
      assert.equal(query, '(prefers-color-scheme: dark)');
      if (options.noMedia) throw new Error('Unavailable');
      return media;
    },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch() { throw new Error('Theme changes must not make requests'); },
  });
  Object.defineProperty(global, 'localStorage', {get() {
    if (options.denyStorage) throw new Error('Denied');
    return storage;
  }});
  global.addEventListener('devgraph:themechange', event => transitions.push(event.detail));
  const context = vm.createContext(global);
  vm.runInContext(source, context);
  const addControls = () => {
    const picker = Object.assign(eventTarget(), {value: ''});
    const attributes = {};
    const status = {textContent: '', attributes, setAttribute(name, value) {attributes[name] = value;}};
    pickers.push(picker); statuses.push(status);
    return {picker, status};
  };
  return {
    api: context.DevgraphTheme, context, doc, dataset, stored, storage, writes, transitions, pickers, statuses, addControls,
    ready() {doc.readyState = 'interactive'; doc.dispatchEvent({type: 'DOMContentLoaded'});},
    system(dark) {
      media.matches = dark;
      if (media.legacyListener) media.legacyListener({matches: dark});
      else media.dispatchEvent({type: 'change', matches: dark});
    },
    storageEvent(value, extra = {}) {
      global.dispatchEvent({type: 'storage', storageArea: storage, key, newValue: value, ...extra});
    },
  };
}

test('head initialization resolves System synchronously before controls or DOMContentLoaded', () => {
  for (const dark of [false, true]) {
    const f = browser({dark});
    assert.equal(f.dataset.theme, dark ? 'dark' : 'light');
    assert.equal(f.dataset.themePreference, 'system');
    assert.equal(f.doc.readyState, 'loading');
    assert.equal(f.writes.length, 0, 'default initialization does not overwrite preferences');
    assert.equal(f.transitions.length, 1);
    assert.equal(f.transitions[0].source, 'init');
  }
});

test('saved valid modes restore exactly; malformed storage safely resolves System', () => {
  for (const saved of ['system', 'light', 'dark', 'aqua']) {
    const f = browser({saved, dark: true});
    assert.equal(f.api.preference, saved);
    assert.equal(f.api.theme, saved === 'system' ? 'dark' : saved);
  }
  for (const saved of ['', 'LIGHT', ' light ', 'null', '"aqua"', '{"theme":"aqua"}', '__proto__']) {
    const f = browser({saved});
    assert.equal(f.api.preference, 'system');
    assert.equal(f.api.theme, 'light');
    assert.equal(f.stored.get(key), saved, 'malformed data is not silently overwritten');
  }
});

test('denied storage keeps an in-memory choice and explains its lifetime', () => {
  for (const options of [{denyStorage: true}, {denyRead: true, denyWrite: true}, {denyWrite: true}]) {
    const f = browser(options), {picker, status} = f.addControls();
    const focus = f.doc.activeElement;
    f.ready(); picker.value = 'aqua'; picker.dispatchEvent({type: 'change'});
    assert.equal(f.api.theme, 'aqua');
    assert.equal(f.api.getState().persisted, false);
    assert.match(status.textContent, /Theme: Aqua.*only this visit/);
    assert.equal(status.attributes['aria-live'], 'polite');
    assert.equal(f.doc.activeElement, focus);
    assert.equal(f.writes.length, 0);
  }
});

test('System follows OS changes; explicit themes ignore them until System is restored', () => {
  const f = browser({saved: 'aqua'});
  f.system(true);
  assert.equal(f.api.theme, 'aqua'); assert.equal(f.transitions.length, 1);
  assert.equal(f.api.setPreference('light'), true);
  f.system(false); f.system(true);
  assert.equal(f.api.theme, 'light'); assert.equal(f.transitions.length, 2);
  f.api.setPreference('system');
  assert.equal(f.api.theme, 'dark');
  f.system(false);
  assert.equal(f.api.theme, 'light');
  assert.equal(f.transitions.at(-1).source, 'system');
  assert.equal(f.stored.get(key), 'system');
  assert.equal(f.writes.length, 2, 'OS changes do not persist a resolved color as an explicit choice');
});

test('cross-tab events synchronize preferences and controls without storage feedback loops', () => {
  const first = browser(), second = browser({dark: true});
  const {picker, status} = second.addControls(); second.ready();
  first.api.setPreference('aqua'); second.storageEvent(first.stored.get(key));
  assert.equal(second.api.preference, first.api.preference);
  assert.equal(second.api.theme, 'aqua'); assert.equal(picker.value, 'aqua');
  assert.equal(status.textContent, 'Theme: Aqua'); assert.equal(second.writes.length, 0);
  second.storageEvent('light', {key: 'another-setting'});
  second.storageEvent('light', {storageArea: {}});
  assert.equal(second.api.theme, 'aqua');
  second.storageEvent(null);
  assert.equal(second.api.preference, 'system'); assert.equal(second.api.theme, 'dark');
  second.storageEvent('light'); second.storageEvent(null, {key: null});
  assert.equal(second.api.preference, 'system', 'clearing local storage restores the default');
  second.storageEvent('aqua'); second.storageEvent('malformed');
  assert.equal(second.api.preference, 'system');
});

test('all ordinary and fullscreen pickers synchronize and bind idempotently', () => {
  const f = browser(), one = f.addControls(), two = f.addControls();
  f.ready(); f.api.bindPickers();
  one.picker.value = 'dark'; one.picker.dispatchEvent({type: 'change'});
  assert.equal(f.writes.length, 1);
  assert.equal(two.picker.value, 'dark');
  assert.equal(one.status.textContent, 'Theme: Dark');
  assert.equal(two.status.textContent, 'Theme: Dark');
  const added = f.addControls(); f.api.bindPickers();
  assert.equal(added.picker.value, 'dark');
  added.picker.value = 'system'; added.picker.dispatchEvent({type: 'change'});
  assert.ok(f.pickers.every(picker => picker.value === 'system'));
  assert.ok(f.statuses.every(status => status.textContent === 'Theme: System (Light)'));
  f.system(true);
  assert.ok(f.statuses.every(status => status.textContent === 'Theme: System (Dark)'));
});

test('invalid API choices and redundant updates emit no theme transitions', () => {
  const f = browser();
  for (const value of [null, undefined, {}, 'sepia', 'Dark']) assert.equal(f.api.setPreference(value), false);
  assert.equal(f.api.preference, 'system'); assert.equal(f.writes.length, 0);
  f.api.setPreference('system'); f.system(false); f.storageEvent('system');
  assert.equal(f.transitions.length, 1);
  f.api.setPreference('light');
  assert.equal(f.transitions.length, 2, 'changing preference is a transition even when the resolved color is unchanged');
  f.api.setPreference('light');
  assert.equal(f.transitions.length, 2);
  assert.deepEqual(Object.keys(f.transitions[1]).sort(), ['preference', 'source', 'theme']);
});

test('legacy media listeners, missing media support, and duplicate script loading are safe', () => {
  const legacy = browser({legacyMedia: true}); legacy.system(true);
  assert.equal(legacy.api.theme, 'dark');
  const fallback = browser({noMedia: true, readyState: 'complete'});
  assert.equal(fallback.api.theme, 'light');
  const initial = fallback.api;
  vm.runInContext(source, fallback.context);
  assert.equal(fallback.context.DevgraphTheme, initial);
  assert.equal(fallback.transitions.length, 1);
});
