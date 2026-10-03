import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { contextWithFunctions } from './monitor_test_helpers.mjs';

test('large maps reuse bounded solid sprites across frames, zoom and unknown kinds', () => {
  const sprites = [], images = [];
  const context = () => new Proxy({
    drawImage(image) { images.push(image); },
    createLinearGradient() { return { addColorStop() {} }; },
  }, { get: (target, key) => target[key] ?? (() => {}) });
  const document = { createElement(tag) {
    assert.equal(tag, 'canvas');
    const ctx = context(), canvas = { getContext: () => ctx };
    sprites.push(canvas); return canvas;
  } };
  const c = contextWithFunctions([], { document, devicePixelRatio: 2, Path2D: class {} });
  vm.runInContext(readFileSync(new URL('../../src/devgraph/frontend/static/topology/canvas.js', import.meta.url), 'utf8'), c);
  const kinds = [...Object.keys(c.Topology.registry), 'Unknown A', 'Unknown B'];
  const nodes = Array.from({ length: 1500 }, (_, i) => ({ key: `n${i}`, kind: kinds[i % kinds.length], title: `Node ${i}` }));
  const view = { width: 1200, height: 800, nodes, edges: [], labels: 'none', projected: new Map(nodes.map((node, i) => [node.key, { x: 20 + i % 40 * 28, y: 20 + Math.floor(i / 40) * 20, radius: 7 }])) };
  const ctx = context(), canvas = { getContext: () => ctx };
  const draw = () => c.DevgraphTopologyCanvas.draw(canvas, view, null, null, new Set(), new Set(), x => x, () => []);
  draw();
  assert.equal(sprites.length, 9, 'one sprite per registered kind and one shared fallback');
  assert.equal(images.length, 1500);
  for (const position of view.projected.values()) position.radius = 14;
  draw();
  assert.equal(sprites.length, 9, 'zoom does not rebuild artwork');
  assert.equal(images.length, 3000);
  assert.equal(new Set(images).size, 9);
  assert.ok(sprites.every(sprite => sprite.width === 256 && sprite.height === 256));
});

test('theme repaint updates canvas contrast without moving nodes or rebuilding solid sprites', () => {
  const sprites = [], images = [], strokes = [], labels = [], backgrounds = [];
  const materialContext = new Proxy({ createLinearGradient: () => ({ addColorStop() {} }) }, { get: (target, key) => target[key] ?? (() => {}) });
  const document = { createElement(tag) {
    assert.equal(tag, 'canvas');
    const sprite = { getContext: () => materialContext }; sprites.push(sprite); return sprite;
  } };
  const c = contextWithFunctions([], { document, devicePixelRatio: 2, Path2D: class {} });
  vm.runInContext(readFileSync(new URL('../../src/devgraph/frontend/static/topology/canvas.js', import.meta.url), 'utf8'), c);
  const ctx = new Proxy({
    drawImage(image) { images.push(image); },
    stroke(path) { strokes.push({ color:this.strokeStyle, width:this.lineWidth, path }); },
    fillRect() { backgrounds.push(this.fillStyle); },
    fillText() { labels.push(this.fillStyle); },
  }, { get: (target, key) => target[key] ?? (() => {}) });
  const nodes = [
    { key:'a', kind:'Proposal', title:'Proposal' },
    { key:'b', kind:'Project', title:'Project' },
    { key:'c', kind:'Task', title:'Task' },
  ];
  const projected = new Map(nodes.map((node, i) => [node.key, { x:60+i*160, y:100, radius:7*(i+1) }]));
  const view = { width:600, height:300, nodes, edges:[{ source:'a', target:'b', relationship:'HAS_CHILD' },{ source:'b', target:'c', relationship:'DEPENDS_ON' }], labels:'selected', projected };
  const positionsBefore = JSON.stringify([...projected]);
  const canvas = { getContext: () => ctx };
  const palettes = [
    { edge:'#527568', edgeActive:'#9dc9b7', labelBackground:'#0b1712', labelText:'#eef6f2', outline:'#fff', selection:'#7cf7cf', nodeBorder:'rgba(233,246,255,.5)' },
    { edge:'#40534e', edgeActive:'#0d7251', labelBackground:'#FFFFFF', labelText:'#0D1A16', outline:'#0D1A16', selection:'#0d7251', nodeBorder:'#40534e' },
    { edge:'#567069', edgeActive:'#0D1A16', labelBackground:'#9BFBE3', labelText:'#131a18', outline:'#193830', selection:'#185647', nodeBorder:'#2c3a36' },
  ];
  for (const [i, paint] of palettes.entries()) {
    if (i) view.paint = paint;
    strokes.length = labels.length = backgrounds.length = 0;
    c.DevgraphTopologyCanvas.draw(canvas, view, 'a', 'a', new Set(['a','b']), new Set(['a','b']), x => x, candidates => candidates.slice(0, 3));
    assert.equal(strokes.find(stroke => !stroke.path)?.color, paint.edgeActive);
    assert.ok(strokes.some(stroke => !stroke.path && stroke.color === paint.edge));
    assert.ok(strokes.some(stroke => stroke.path && stroke.color === paint.outline && stroke.width === 3/7));
    assert.ok(strokes.some(stroke => !stroke.path && stroke.color === paint.selection && stroke.width === 1.5/7));
    assert.deepEqual(strokes.filter(stroke => stroke.path && stroke.color === paint.nodeBorder).map(stroke => stroke.width), [1/7,1/14,1/21], 'silhouette borders stay one CSS pixel at each node radius');
    assert.ok(labels.length > 0);
    assert.ok(labels.every(color => color === paint.labelText));
    assert.ok(backgrounds.every(color => color === paint.labelBackground));
    assert.equal(sprites.length, 3, 'theme changes reuse the same three material sprites');
    assert.equal(new Set(images).size, 3);
    assert.equal(view.projected, projected);
    assert.equal(JSON.stringify([...projected]), positionsBefore, 'repainting does not mutate node positions or size');
  }
});
