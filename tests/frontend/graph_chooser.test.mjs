import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
import {treeDocument} from './monitor_test_helpers.mjs';

const require = createRequire(import.meta.url);
const {create, placement, navigationIndex} = require('../../src/devgraph/frontend/static/topology/chooser.js');
const items = count => Array.from({length: count}, (_, index) => ({key: `Task/${index}`, id: `${index}`, kind: 'Task', title: `Item ${index}`, status: 'in_progress'}));

function events(target = {}) {
  const listeners = new Map();
  Object.assign(target, {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type, init = {}) {
      const event = {type, target, prevented: false, stopped: false,
        preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}, ...init};
      for (const fn of listeners.get(type) || []) fn(event);
      return event;
    },
  });
  return target;
}

function fixture({height = 420, width = 800} = {}) {
  const doc = events(treeDocument()), win = events({innerWidth: 1200, innerHeight: 800});
  doc.defaultView = win;
  const originalCreate = doc.createElement;
  doc.createElement = tag => {
    const element = events(originalCreate(tag)), classes = new Set();
    element.ownerDocument = doc; element.style = {}; element.scrollLeft = 0; element.scrollTop = 0;
    element.clientTop = element.clientLeft = 0; element.isConnected = true;
    element.classList = {add(value) {classes.add(value);}, contains(value) {return classes.has(value);}};
    element.closest = () => null;
    element.getBoundingClientRect = () => ({left: 0, top: 0, width: 100, height: 50, bottom: 50, right: 100});
    return element;
  };
  const container = doc.createElement('section'), panel = doc.createElement('div'), opener = doc.createElement('svg');
  container.clientWidth = width; container.clientHeight = height;
  container.getBoundingClientRect = () => ({left: 100, top: 150, width: container.clientWidth, height: container.clientHeight});
  panel.getBoundingClientRect = () => {
    const w = Math.min(360, parseFloat(panel.style.width) || 360), h = Math.min(500, parseFloat(panel.style.maxHeight) || 500);
    const left = 100 + (parseFloat(panel.style.left) || 0), top = 150 + (parseFloat(panel.style.top) || 0);
    panel.clientHeight = h;
    return {left, top, width: w, height: h, right: left + w, bottom: top + h};
  };
  opener.focus(); container.append(panel);
  const selections = [], closes = [], icons = [];
  const controller = create({panel, container, onSelect(item, options) {selections.push({item, options});},
    onClose(value) {closes.push(value);}, renderIcon(item) {icons.push(item.key); return doc.createElement('svg');}});
  const buttons = () => panel.descendants().filter(node => node.className === 'dg-chooser__item');
  return {doc, win, container, panel, opener, controller, selections, closes, icons, buttons,
    open(data = items(3)) {return controller.open(data, {clientX: 890, clientY: 560, returnFocus: opener});}};
}

test('placement flips at an edge and clamps measured dimensions within a partly visible container', () => {
  const bounds = {left: 100, top: 100, width: 500, height: 350};
  const result = placement({bounds, viewport: {left: 0, top: 0, width: 800, height: 400}, point: {x: 590, y: 390}, size: {width: 260, height: 180}});
  assert.equal(result.left, 220); assert.equal(result.top, 100);
  assert.equal(result.maxHeight, 284);
  assert.ok(bounds.left + result.left >= 108);
  assert.ok(bounds.top + result.top + result.height <= 392);
});

test('placement remains bounded for narrow, short, and offscreen viewports', () => {
  for (const width of [0, 8, 40, 180, 1000]) for (const height of [0, 12, 50, 100, 600]) {
    const bounds = {left: -25, top: -15, width: width + 25, height: height + 15};
    const p = placement({bounds, viewport: {left: 0, top: 0, width, height}, point: {x: 999, y: 999}, size: {width: 360, height: 440}});
    assert.ok(p.width <= width && p.height <= height);
    assert.ok(p.left + bounds.left >= 0 && p.top + bounds.top >= 0);
    assert.ok(p.left + bounds.left + p.width <= width);
    assert.ok(p.top + bounds.top + p.height <= height);
  }
});

test('keyboard navigation handles limits and leaves Tab and ordinary native activation alone', () => {
  assert.equal(navigationIndex('ArrowDown', 19, 20), 0);
  assert.equal(navigationIndex('ArrowUp', 0, 20), 19);
  assert.equal(navigationIndex('ArrowDown', -1, 3), 0);
  assert.equal(navigationIndex('ArrowUp', -1, 3), 2);
  assert.equal(navigationIndex('Home', 2, 3), 0);
  assert.equal(navigationIndex('End', 0, 3), 2);
  for (const key of ['Tab', 'Enter', ' ', 'Escape']) assert.equal(navigationIndex(key, 0, 3), null);
  assert.equal(navigationIndex('ArrowDown', 0, 0), null);
});

test('open caps choices while keeping true counts, duplicate identity, text safety, and short-container fit', () => {
  const f = fixture({height: 72, width: 240}), data = items(34);
  data[0].title = data[1].title = '<strong>Same title</strong>';
  assert.equal(f.open(data), true);
  assert.equal(f.buttons().length, 20); assert.equal(f.icons.length, 20);
  assert.match(f.panel.textContent, /34 items here/);
  assert.match(f.panel.textContent, /Showing 20 of 34/);
  assert.equal(f.buttons()[0].querySelector('.dg-chooser__name').textContent, '<strong>Same title</strong>');
  assert.equal(f.buttons()[0].querySelector('.dg-chooser__id').textContent, '0');
  assert.equal(f.buttons()[1].querySelector('.dg-chooser__id').textContent, '1');
  assert.ok(parseFloat(f.panel.style.maxHeight) <= 56);
  assert.ok(parseFloat(f.panel.style.top) + f.panel.getBoundingClientRect().height <= 72);
});

test('reopening during refresh preserves DOM, focus, choice snapshot, and scroll', () => {
  const f = fixture(), data = items(3); f.open(data);
  const original = f.buttons(), second = original[1]; second.focus(); f.panel.scrollTop = 50;
  data[1].title = 'Changed in refreshed graph';
  assert.equal(f.controller.open(items(9), {clientX: 300, clientY: 300}), false);
  assert.equal(f.buttons()[1], second); assert.equal(f.doc.activeElement, second);
  assert.equal(f.panel.scrollTop, 50); assert.equal(f.icons.length, 3);
  second.emit('click');
  assert.equal(f.selections[0].item.title, 'Item 1');
  assert.equal(f.selections[0].options.modal, false);
  assert.equal(f.controller.isOpen, false);
  assert.equal(f.doc.activeElement, second, 'selection callback owns reader focus; no intermediate opener focus');
});

test('arrows move focus; Escape restores opener without escaping into graph handlers', () => {
  const f = fixture(); f.open();
  let event = f.panel.emit('keydown', {key: 'End'});
  assert.equal(f.doc.activeElement, f.buttons()[2]); assert.equal(event.prevented, true); assert.equal(event.stopped, true);
  f.panel.emit('keydown', {key: 'ArrowDown'}); assert.equal(f.doc.activeElement, f.buttons()[0]);
  event = f.panel.emit('keydown', {key: 'Tab'});
  assert.equal(event.prevented, false); assert.equal(event.stopped, false);
  event = f.panel.emit('keydown', {key: 'Escape'});
  assert.equal(f.controller.isOpen, false); assert.equal(f.doc.activeElement, f.opener);
  assert.equal(event.prevented, true); assert.equal(event.stopped, true);
  assert.deepEqual(f.closes, [{reason: 'escape', restoreFocus: true}]);
});

test('outside pointer and normal Tab dismissal never steal the destination focus', () => {
  const f = fixture(), outside = f.doc.createElement('input');
  f.open(); const originalFocus = f.doc.activeElement;
  f.doc.emit('pointerdown', {target: outside});
  assert.equal(f.doc.activeElement, originalFocus, 'dismissal does not focus the opener before the native pointer action');
  outside.focus();
  assert.equal(f.controller.isOpen, false); assert.equal(f.doc.activeElement, outside);
  assert.equal(f.closes.at(-1).restoreFocus, false);
  f.open(); outside.focus(); f.doc.emit('focusin', {target: outside});
  assert.equal(f.controller.isOpen, false); assert.equal(f.doc.activeElement, outside);
  f.open(); f.doc.emit('pointerdown', {target: f.buttons()[1]});
  assert.equal(f.controller.isOpen, true);
});

test('right-click and keyboard context actions request the modal once', () => {
  for (const action of ['contextmenu', 'ContextMenu', 'F10']) {
    const f = fixture(); f.open(); const button = f.buttons()[1];
    const event = action === 'contextmenu' ? button.emit('contextmenu') : button.emit('keydown', {key: action, shiftKey: action === 'F10'});
    assert.equal(f.selections.length, 1); assert.equal(f.selections[0].options.modal, true);
    assert.equal(f.selections[0].item.key, 'Task/1'); assert.equal(f.controller.isOpen, false);
    assert.equal(event.prevented, true); assert.equal(event.stopped, true);
    button.emit('click'); assert.equal(f.selections.length, 1, 'a trailing synthetic/native click cannot select twice');
  }
});

test('Close restores an available opener, while empty data or a disconnected opener leaves focus alone', () => {
  const f = fixture();
  assert.equal(f.controller.open([]), false); assert.equal(f.doc.activeElement, f.opener);
  f.open(); f.panel.querySelector('.dg-chooser__close').emit('click');
  assert.equal(f.doc.activeElement, f.opener); assert.equal(f.controller.isOpen, false);
  f.open(); const previous = f.doc.activeElement; f.opener.isConnected = false;
  f.controller.close(); assert.equal(f.doc.activeElement, previous);
});

test('resize repositions without rebuilding; destroy removes listeners and prevents reopen', () => {
  const f = fixture(); f.open(); const first = f.buttons()[0];
  f.container.clientHeight = 55; f.win.emit('resize');
  assert.ok(parseFloat(f.panel.style.maxHeight) <= 39);
  assert.equal(f.buttons()[0], first);
  f.controller.destroy();
  const count = f.closes.length;
  f.doc.emit('pointerdown', {target: f.opener}); f.win.emit('resize');
  assert.equal(f.closes.length, count); assert.equal(f.open(), false);
});
