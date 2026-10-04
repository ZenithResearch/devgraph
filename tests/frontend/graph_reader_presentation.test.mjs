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
  const c = contextWithFunctions(['syncReaderPresentation', 'setReaderCollapsed', 'setReaderMode'], {
    document, detailState: {node: {key:'Project:example'}}, modalRequests: [],
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

test('resizing back to a desktop split restores map interaction without changing reading focus', () => {
  const p = presentation(760);
  p.document.activeElement = p.title;
  p.c.syncReaderPresentation();
  p.layout.clientWidth = 1000;
  p.c.syncReaderPresentation();
  assert.equal(p.map.inert, false);
  assert.equal(p.document.activeElement, p.title);
});

test('the optional modal action never switches to a graph-replacing reading view', () => {
  const p = presentation();
  p.c.openNodeReaderModal = (node, trigger) => p.c.modalRequests.push({node,trigger});
  p.c.setReaderMode();
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.c.modalRequests.length, 1);
  assert.equal(p.c.modalRequests[0].node.key, 'Project:example');
  assert.equal(p.c.modalRequests[0].trigger, p.document.getElementById('reader-mode'));
  assert.equal(p.scrolls(), 0);
});
