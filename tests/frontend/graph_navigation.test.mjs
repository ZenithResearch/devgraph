import assert from 'node:assert/strict';
import test from 'node:test';
import { contextWithFunctions } from './monitor_test_helpers.mjs';

function navigation() {
  return contextWithFunctions(['neighborhoodKeys', 'chooseGraphLabels'], {});
}

test('neighborhood contains the selected node and immediate neighbors in either direction only', () => {
  const c = navigation();
  const nodes = ['selected', 'parent', 'child', 'second-hop', 'isolated'].map(key => ({ key }));
  const edges = [
    { source: 'parent', target: 'selected' },
    { source: 'selected', target: 'child' },
    { source: 'child', target: 'second-hop' },
    { source: 'selected', target: 'deleted-target' },
    { source: 'selected', target: 'selected' },
  ];
  assert.deepEqual([...c.neighborhoodKeys(nodes, edges, 'selected')].sort(), ['child', 'parent', 'selected']);
  assert.deepEqual([...c.neighborhoodKeys(nodes, edges, 'isolated')], ['isolated']);
  assert.equal(c.neighborhoodKeys(nodes, edges, 'absent').size, 0);
});

test('label placement rejects overlaps with node markers, prior labels, and viewport bounds', () => {
  const c = navigation();
  const candidates = [
    { key: 'on-node', x: 12, y: 12, width: 40, height: 16 },
    { key: 'first', x: 80, y: 12, width: 70, height: 16 },
    { key: 'colliding', x: 90, y: 20, width: 75, height: 16 },
    { key: 'outside-left', x: -20, y: 60, width: 75, height: 16 },
    { key: 'outside-right', x: 280, y: 60, width: 75, height: 16 },
    { key: 'outside-bottom', x: 100, y: 190, width: 75, height: 16 },
    { key: 'second', x: 170, y: 40, width: 70, height: 16 },
    { key: 'invalid', x: NaN, y: 90, width: 70, height: 16 },
  ];
  const occupied = [{ x: 10, y: 10, width: 20, height: 20 }];
  const before = structuredClone(occupied);
  const result = c.chooseGraphLabels(candidates, occupied, 300, 200);
  assert.deepEqual(Array.from(result, label => label.key), ['first', 'second']);
  assert.deepEqual(occupied, before, 'placement must not change the caller’s marker geometry');
});

test('a rejected label can use a later free position, but each logical label appears only once', () => {
  const c = navigation();
  const candidates = [
    { key: 'title', x: 8, y: 8, width: 100, height: 20 },
    { key: 'title', x: 80, y: 80, width: 100, height: 20 },
    { key: 'title', x: 80, y: 120, width: 100, height: 20 },
  ];
  const accepted = c.chooseGraphLabels(candidates, [{ x: 10, y: 10, width: 30, height: 30 }], 300, 200);
  assert.equal(accepted.length, 1);
  assert.equal(accepted[0], candidates[1]);
});

test('dense graph labels keep a gap without dropping every readable relationship label', () => {
  const c = navigation();
  const candidates = Array.from({ length: 46 }, (_, index) => ({
    key: String(index), x: 60 + (index % 10) * 20, y: 40 + Math.floor(index / 10) * 20,
    width: 75, height: 16,
  }));
  const accepted = c.chooseGraphLabels(candidates, [], 390, 300);
  assert.ok(accepted.length > 0 && accepted.length < candidates.length);
  for (let i = 0; i < accepted.length; i += 1) {
    for (const b of accepted.slice(i + 1)) {
      const a = accepted[i];
      assert.ok(a.x + a.width + 4 <= b.x || b.x + b.width + 4 <= a.x || a.y + a.height + 4 <= b.y || b.y + b.height + 4 <= a.y);
    }
  }
});
