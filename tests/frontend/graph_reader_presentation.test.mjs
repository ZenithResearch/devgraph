import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions, treeDocument } from './monitor_test_helpers.mjs';

function presentation(width = 1000) {
  const document = treeDocument();
  const surface = document.getElementById('graph-surface');
  const panel = document.getElementById('graph-details');
  const svg = document.getElementById('graph-svg');
  const title = document.getElementById('reader-title');
  const layout = document.createElement('div'); layout.className = 'graph-layout'; layout.clientWidth = width;
  const map = document.createElement('div'); map.className = 'graph-scroll';
  document.querySelector = () => map;
  surface.append(layout); layout.append(map, panel); map.append(svg); panel.append(title);
  for (const el of [surface, panel]) {
    const classes = new Set();
    el.classList = { contains: value => classes.has(value), remove: value => classes.delete(value),
      toggle(value, enabled) { if (enabled) classes.add(value); else classes.delete(value); } };
  }
  let saves = 0, scrolls = 0;
  surface.scrollIntoView = () => { scrolls += 1; };
  svg.focus = () => { assert.equal(map.inert, false, 'restore focus only after the map becomes interactive'); document.activeElement = svg; };
  const c = contextWithFunctions(['syncReaderPresentation', 'setReaderCollapsed', 'setReaderMode', 'handleReaderKeydown'], {
    document, detailState: {node: {key:'Project:example'}}, modalRequests: [],
    readerModal: {active:false},
    openNodeReaderModal(node,trigger) { this.modalRequests.push({node,trigger}); }, window: { requestAnimationFrame: callback => callback() },
    saveGraphPreferences() { saves += 1; },
    text(id, value) { document.getElementById(id).textContent = value; },
  });
  return {c, document, surface, panel, svg, title, layout, map, saves: () => saves, scrolls: () => scrolls};
}

test('narrow reader keeps the graph interactive and close restores focus and saves preference', () => {
  const p = presentation(760);
  p.document.activeElement = p.svg;
  p.c.syncReaderPresentation();
  assert.equal(p.map.inert, false);
  assert.equal(p.document.activeElement, p.svg);
  p.document.activeElement = p.title;
  p.c.setReaderCollapsed(true);
  assert.equal(p.map.inert, false);
  assert.equal(p.document.activeElement, p.svg);
  assert.equal(p.document.getElementById('reader-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(p.saves(), 1);
  p.c.setReaderCollapsed(true);
  assert.equal(p.saves(), 1, 'unchanged presentation must not write storage again');
});

test('expanding a narrow reader preserves its scroll, and Back to graph restores focus without saving preferences', () => {
  const p = presentation(760), body = p.document.getElementById('reader-body'); body.scrollTop = 320;
  p.c.syncReaderPresentation();
  assert.equal(p.document.getElementById('reader-mode').textContent, 'Expand reader');
  p.c.setReaderMode();
  assert.equal(p.surface.classList.contains('reading-view'), true);
  assert.equal(p.map.inert, true);
  assert.equal(p.document.activeElement, p.title);
  assert.equal(p.document.getElementById('reader-mode').textContent, 'Back to graph');
  assert.equal(p.document.getElementById('reader-mode').getAttribute('aria-expanded'), 'true');
  assert.equal(body.scrollTop, 320);
  assert.equal(p.c.modalRequests.length, 0);
  p.c.setReaderMode();
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.map.inert, false);
  assert.equal(p.document.activeElement, p.svg);
  assert.equal(body.scrollTop, 320);
  assert.equal(p.document.getElementById('reader-mode').getAttribute('aria-expanded'), 'false');
  assert.equal(p.saves(), 0, 'temporary reading mode must not change saved collapse preferences');
});

test('resizing an expanded reader back to a desktop split restores map interaction without changing reading focus', () => {
  const p = presentation(760);
  p.c.setReaderMode();
  p.layout.clientWidth = 1000;
  p.c.syncReaderPresentation();
  assert.equal(p.map.inert, false);
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.document.activeElement, p.title);
  assert.equal(p.document.getElementById('reader-mode').textContent, 'Open modal');
  assert.equal(p.document.getElementById('reader-mode').hasAttribute('aria-expanded'), false);
  p.layout.clientWidth = 760; p.c.syncReaderPresentation();
  assert.equal(p.map.inert, false, 'returning to a narrow screen starts in the stacked view');
});

test('closing an expanded reader restores the map before moving focus into it', () => {
  const p = presentation(760); p.c.setReaderMode(); p.c.setReaderCollapsed(true);
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.map.inert, false);
  assert.equal(p.document.activeElement, p.svg);
  assert.equal(p.saves(), 1);
  p.c.setReaderCollapsed(false);
  assert.equal(p.map.inert, false);
  assert.equal(p.document.getElementById('reader-mode').textContent, 'Expand reader');
});

test('Escape returns from expanded reading without exiting fullscreen or intercepting a modal', () => {
  const p = presentation(760); p.surface.classList.toggle('is-fullscreen', true); p.c.setReaderMode();
  let prevented=0, stopped=0;
  const event={key:'Escape',preventDefault(){prevented++;},stopPropagation(){stopped++;}};
  p.c.readerModal.active=true; p.c.handleReaderKeydown(event);
  assert.equal(prevented,0); assert.equal(p.map.inert,true);
  p.c.readerModal.active=false; p.c.handleReaderKeydown(event);
  assert.equal(prevented,1); assert.equal(stopped,1);
  assert.equal(p.map.inert,false); assert.equal(p.surface.classList.contains('is-fullscreen'),true);
  assert.equal(p.scrolls(),0,'fullscreen does not scroll the background page');
});

test('the wide reader keeps its optional modal action', () => {
  const p = presentation();
  p.c.openNodeReaderModal = (node, trigger) => p.c.modalRequests.push({node,trigger});
  p.c.setReaderMode();
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.c.modalRequests.length, 1);
  assert.equal(p.c.modalRequests[0].node.key, 'Project:example');
  assert.equal(p.c.modalRequests[0].trigger, p.document.getElementById('reader-mode'));
  assert.equal(p.scrolls(), 0);
});
