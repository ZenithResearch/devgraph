import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {treeDocument, deferred} from './monitor_test_helpers.mjs';

function fixture() {
  const doc = treeDocument(), listeners = new Map(), changes = [], notices = [];
  const make = tag => {
    const element = doc.createElement(tag), classes = new Set();
    element.ownerDocument = doc; element.inert = false; element.style = {}; element.tabIndex = 0;
    element.classList = {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)};
    element.getClientRects = () => [1];
    Object.defineProperty(element, 'isConnected', {get: () => doc.body.contains(element)});
    return element;
  };
  doc.createComment = () => make('#comment');
  doc.addEventListener = (name, fn) => listeners.set(name, fn);
  doc.defaultView = {scrollX: 0, scrollY: 500, scrollTo(value) {this.restored = value;}};
  doc.body = make('body'); doc.body.style.overflow = 'auto';
  const page = make('main'), sibling = make('aside'), surface = make('section'), button = make('button'), canvas = make('svg'), hidden = make('button');
  hidden.getClientRects = () => []; sibling.inert = true;
  surface.append(button, canvas, hidden); page.append(surface); doc.body.append(page, sibling);
  surface.querySelectorAll = () => [button, canvas, hidden]; button.focus();
  const c = vm.createContext({});
  vm.runInContext(readFileSync(new URL('../../src/devgraph/frontend/static/topology/surface.js', import.meta.url), 'utf8'), c);
  const controller = c.DevgraphGraphSurface.create({element: surface, button, onChange: value => changes.push(value), announce: text => notices.push(text)});
  const send = (name, event = {}) => listeners.get(name)(event);
  return {doc, page, sibling, surface, button, canvas, controller, changes, notices, send};
}

test('fullscreen isolates and restores the same canvas, prior inert state, focus and scroll', async () => {
  const f = fixture();
  await f.controller.enter();
  assert.equal(f.surface.parentNode, f.doc.body);
  assert.equal(f.page.inert, true); assert.equal(f.sibling.inert, true);
  assert.equal(f.doc.body.style.overflow, 'hidden');
  assert.equal(f.button.textContent, 'Exit full screen');
  assert.equal(f.surface.children[1], f.canvas);
  await f.controller.exit();
  assert.equal(f.surface.parentNode, f.page);
  assert.equal(f.page.children.length, 1);
  assert.equal(f.page.inert, false); assert.equal(f.sibling.inert, true);
  assert.equal(f.doc.body.style.overflow, 'auto');
  assert.equal(f.doc.activeElement, f.button);
  assert.equal(f.doc.defaultView.restored.top, 500);
  assert.deepEqual(f.changes, [true, false]);
});

test('denied native fullscreen retains the viewport fallback and Escape exits it', async () => {
  const f = fixture(); f.surface.requestFullscreen = async () => {throw new Error('Unavailable in embedded browser');};
  await f.controller.enter();
  assert.equal(f.controller.active, true); assert.equal(f.surface.classList.contains('is-fullscreen'), true);
  let prevented = false;
  f.send('keydown', {key: 'Escape', preventDefault() {prevented = true;}});
  assert.equal(prevented, true); assert.equal(f.controller.active, false);
});

test('native fullscreen escape synchronizes the panel and button', async () => {
  const f = fixture();
  f.surface.requestFullscreen = async () => {f.doc.fullscreenElement = f.surface; f.send('fullscreenchange');};
  await f.controller.enter();
  f.doc.fullscreenElement = null; f.send('fullscreenchange');
  assert.equal(f.controller.active, false); assert.equal(f.button.textContent, 'Full screen');
  assert.equal(f.surface.parentNode, f.page);
});

test('an exit while native entry is pending cannot leave a stuck fullscreen element', async () => {
  const f = fixture(), request = deferred();
  f.surface.requestFullscreen = () => request.promise;
  f.doc.exitFullscreen = async () => {f.doc.fullscreenElement = null; f.send('fullscreenchange');};
  const entering = f.controller.enter(); await f.controller.exit();
  f.doc.fullscreenElement = f.surface; f.send('fullscreenchange'); request.resolve(); await entering;
  assert.equal(f.controller.active, false); assert.equal(f.doc.fullscreenElement, null);
  assert.equal(f.surface.parentNode, f.page); assert.equal(f.page.inert, false);
});

test('Tab wraps inside the fullscreen surface and skips hidden controls', async () => {
  const f = fixture(); await f.controller.enter(); let prevented = 0;
  f.button.focus(); f.send('keydown', {key: 'Tab', shiftKey: true, preventDefault() {prevented++;}});
  assert.equal(f.doc.activeElement, f.canvas);
  f.send('keydown', {key: 'Tab', shiftKey: false, preventDefault() {prevented++;}});
  assert.equal(f.doc.activeElement, f.button); assert.equal(prevented, 2);
});
