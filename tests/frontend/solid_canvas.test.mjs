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
