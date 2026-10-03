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
    document, window: { requestAnimationFrame: callback => callback() },
    saveGraphPreferences() { saves += 1; },
    text(id, value) { document.getElementById(id).textContent = value; },
  });
  return {c, document, surface, panel, svg, title, layout, map, saves: () => saves, scrolls: () => scrolls};
}

test('narrow reader disables covered map and close restores focus and saves preference', () => {
  const p = presentation(760);
  p.document.activeElement = p.svg;
  p.c.syncReaderPresentation();
  assert.equal(p.map.inert, true);
  assert.equal(p.document.activeElement, p.title);
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

test('expanded reader and return scroll the surface into view and focus its navigation', () => {
  const p = presentation();
  p.c.setReaderMode(true);
  assert.equal(p.surface.classList.contains('reading-view'), true);
  assert.equal(p.document.activeElement, p.title);
  assert.equal(p.scrolls(), 1);
  p.c.setReaderMode(false);
  assert.equal(p.surface.classList.contains('reading-view'), false);
  assert.equal(p.document.activeElement, p.document.getElementById('reader-mode'));
  assert.equal(p.scrolls(), 2);
  p.surface.classList.toggle('is-fullscreen', true);
  p.c.setReaderMode(true);
  assert.equal(p.scrolls(), 2, 'fullscreen reading must not scroll the underlying page');
});
