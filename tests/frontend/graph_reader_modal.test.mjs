import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {contextWithFunctions, treeDocument} from './monitor_test_helpers.mjs';

function fixture(collapsed = true) {
  const doc = treeDocument();
  const create = doc.createElement;
  doc.createElement = tag => {
    const el = create(tag);
    el.ownerDocument = doc; el.style = {}; el.tabIndex = 0;
    el.classList = {
      contains: name => (el.className || '').split(' ').includes(name),
      remove: name => { el.className = (el.className || '').split(' ').filter(x => x !== name).join(' '); },
      toggle(name, value) { this.remove(name); if (value) el.className += ' ' + name; },
    };
    el.closest = () => el.inert ? el : null;
    el.getClientRects = () => el.hidden ? [] : [1];
    Object.defineProperty(el, 'isConnected', {get: () => doc.documentElement.contains(el)});
    return el;
  };
  const make = tag => doc.createElement(tag);
  doc.documentElement = make('html'); doc.documentElement.style.overflow = 'auto';
  const layout = make('div'), panel = make('aside'), dialog = make('dialog'), trigger = make('button');
  const closeButton = make('button'), modeButton = make('button'), title = make('h3'), last = make('summary'), fallbackFocus = make('svg');
  panel.className = `graph-details${collapsed ? ' collapsed' : ''}`; title.tabIndex = -1;
  modeButton.hidden = false; closeButton.textContent = collapsed ? 'Open reader' : 'Close reader';
  panel.append(closeButton, modeButton, title, last); layout.append(panel);
  doc.documentElement.append(layout, dialog, trigger, fallbackFocus);
  dialog.showModal = () => { dialog.open = true; }; dialog.close = () => { dialog.open = false; };
  dialog.querySelectorAll = () => [closeButton, modeButton, title, last];
  let restores = 0;
  const context = vm.createContext({});
  vm.runInContext(readFileSync(new URL('../../src/devgraph/frontend/static/topology/reader.js', import.meta.url), 'utf8'), context);
  const modal = context.DevgraphReaderModal.create({dialog, panel, closeButton, modeButton, title, fallbackFocus, onRestore() { restores++; }});
  return {doc, layout, panel, dialog, trigger, title, closeButton, modeButton, last, fallbackFocus, modal, restores: () => restores,
    send(name, event = {}) { dialog.listeners.get(name)(event); }};
}

test('modal borrows the same reader and restores layout, collapse, scroll lock and focus', () => {
  for (const collapsed of [true, false]) {
    const f = fixture(collapsed); f.modal.open(f.trigger);
    assert.equal(f.panel.parentNode, f.dialog);
    assert.equal(f.layout.children.length, 1, 'placeholder preserves the graph columns');
    assert.equal(f.layout.children[0].classList.contains('collapsed'), collapsed);
    assert.equal(f.panel.classList.contains('collapsed'), false);
    assert.equal(f.modeButton.hidden, true);
    assert.equal(f.doc.activeElement, f.title);
    assert.equal(f.doc.documentElement.style.overflow, 'hidden');
    f.modal.close();
    assert.equal(f.layout.children[0], f.panel);
    assert.equal(f.panel.classList.contains('collapsed'), collapsed);
    assert.equal(f.modeButton.hidden, false);
    assert.equal(f.doc.documentElement.style.overflow, 'auto');
    assert.equal(f.doc.activeElement, f.trigger);
    assert.equal(f.restores(), 1);
  }
});

test('Escape closes the reader without bubbling to fullscreen, and Tab wraps inside it', () => {
  const f = fixture(); f.modal.open(f.trigger); let prevented = 0, stopped = 0;
  const event = key => ({key, preventDefault() { prevented++; }, stopPropagation() { stopped++; }});
  f.last.focus(); f.send('keydown', event('Tab')); assert.equal(f.doc.activeElement, f.closeButton);
  f.send('keydown', {...event('Tab'), shiftKey: true}); assert.equal(f.doc.activeElement, f.last);
  f.send('keydown', event('Escape')); assert.equal(f.modal.active, false);
  assert.equal(prevented, 3); assert.equal(stopped, 3);
});

test('backdrop click closes only when the gesture began on the backdrop', () => {
  const f = fixture(); f.modal.open(f.trigger);
  f.send('pointerdown', {target:f.panel}); f.send('click', {target:f.dialog}); assert.equal(f.modal.active, true);
  f.send('pointerdown', {target:f.dialog}); f.send('click', {target:f.dialog}); assert.equal(f.modal.active, false);
});

test('a late native close event cannot restore a newly reopened reader', () => {
  const f = fixture(); f.modal.open(f.trigger); f.modal.close(); f.modal.open(f.trigger);
  f.send('close'); assert.equal(f.panel.parentNode, f.dialog); assert.equal(f.modal.active, true);
  f.trigger.remove(); f.modal.close(); assert.equal(f.doc.activeElement, f.fallbackFocus);
});

test('failed native modal entry restores the reader and page scroll', () => {
  const f = fixture(); f.dialog.showModal = () => { throw Error('denied'); };
  assert.throws(() => f.modal.open(f.trigger), /denied/);
  assert.equal(f.panel.parentNode, f.layout); assert.equal(f.doc.documentElement.style.overflow, 'auto');
});

test('context targeting supports all eight node types, node parts, canvas hits and labels', () => {
  const kinds = ['Arena', 'Proposal', 'Initiative', 'Project', 'Issue', 'Task', 'InitiativeObservation', 'EventReceipt'];
  const nodes = kinds.map(kind => ({key:`${kind}:one`,kind}));
  let hits = [];
  const svg = {contains: target => target.inGraph};
  const c = contextWithFunctions(['graphContextNode', 'openNodeContextMenu', 'openNodeKeyboardMenu'], {
    graphSvg: svg, graphView: {index:{byKey:new Map(nodes.map(node => [node.key,node]))},drawnLabels:[]},
    state:{snapshot:{graph_nodes:nodes},selectedGraphKey:nodes[0].key},
    pickGraphNodes: () => hits, graphClientPoint:(x,y) => ({x,y}),
  });
  const target = key => ({inGraph:true,closest: () => key ? {dataset:{nodeKey:key}} : null});
  for (const node of nodes) assert.equal(c.graphContextNode({target:target(node.key)}), node);
  hits = [{key:nodes[3].key}]; assert.equal(c.graphContextNode({target:target()}), nodes[3]);
  hits = []; c.graphView.drawnLabels = [{key:`node:${nodes[2].key}`,x:20,y:20,width:100,height:20}];
  assert.equal(c.graphContextNode({target:target(),clientX:25,clientY:30}), nodes[2]);
  let opened = null, prevented = false;
  c.openNodeReaderModal = node => {opened = node;};
  c.openNodeContextMenu({target:target(),clientX:500,clientY:500,preventDefault() {prevented=true;}});
  assert.equal(opened, null); assert.equal(prevented, false, 'blank canvas keeps its native context menu');
  c.openNodeKeyboardMenu({key:'F10',shiftKey:true,target:target(nodes[5].key),preventDefault(){},stopPropagation(){}});
  assert.equal(opened, nodes[5]);
});
