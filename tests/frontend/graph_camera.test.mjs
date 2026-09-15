import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const frontend = readFileSync(new URL('../../src/devgraph/frontend/app.py', import.meta.url), 'utf8');
const functionNames = [
  'projectGraphPoint', 'graphViewportScale', 'syncZoomControls', 'setGraphZoom',
  'fitGraph', 'resizeGraphViewport', 'setGraphHeight', 'graphScreenVector',
  'filterGraph', 'filterGraphByArena', 'neighborhoodKeys', 'updateGraphVisibility', 'pauseGraphOrbit', 'toggleGraphOrbit',
  'graphOrbitFrame', 'graphDragMoved',
];
const source = functionNames.map(name => {
  const start = frontend.indexOf(`    function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist in the shipped frontend`);
  const end = frontend.indexOf('\n    }', start);
  return frontend.slice(start, end + 6);
}).join('\n');

function camera(width = 1080, height = 420) {
  const elements = new Map();
  const context = vm.createContext({
    graphView: {
      yaw: -.38, pitch: .22, zoom: 1, panX: 0, panY: 0, width, height, fitted: false,
      nodes: [], edges: [],
    },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        clientWidth: width, clientHeight: height, attributes: {}, properties: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        style: { setProperty(name, value) { elements.get(id).properties[name] = value; } },
      });
      return elements.get(id);
    } },
    text() {}, renderGraph() {}, renderGraphSelection() {}, renderGraphSearch() {}, renderArenaFilter() {},
    graphCoordinates(nodes) { return new Map(nodes.map(node => [node.key, node.point])); },
  });
  vm.runInContext(source, context);
  return context;
}

function near(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) < 1e-7, `${message}: ${actual} != ${expected}`);
}

test('zoom keeps the graph point under an off-center cursor fixed, including at zoom limits', () => {
  const c = camera(320, 280);
  Object.assign(c.graphView, { panX: 71, panY: -39 });
  const point = { x: 140, y: -72, z: 91 };
  for (const zoom of [.25, .8, 4, 100, -1]) {
    const anchor = c.projectGraphPoint(point);
    c.setGraphZoom(zoom, anchor);
    const after = c.projectGraphPoint(point);
    near(after.x, anchor.x, 'cursor x');
    near(after.y, anchor.y, 'cursor y');
    assert.ok(c.graphView.zoom >= .25 && c.graphView.zoom <= 4);
  }
});

test('drag inverse produces the requested screen displacement at 25% zoom on a narrow viewport', () => {
  for (const [width, height] of [[220, 280], [1080, 420], [1800, 1000]]) {
    const c = camera(width, height);
    for (const zoom of [.25, 1, 4]) {
      for (const [yaw, pitch] of [[0, 0], [-.38, .22], [2.3, 1.15]]) {
        Object.assign(c.graphView, { zoom, yaw, pitch });
        const point = { x: 161, y: -86, z: 104 };
        const before = c.projectGraphPoint(point);
        const delta = c.graphScreenVector(31, -17, before.scale);
        const after = c.projectGraphPoint({
          x: point.x + delta.x, y: point.y + delta.y, z: point.z + delta.z,
        });
        near(after.x - before.x, 31, 'drag x');
        near(after.y - before.y, -17, 'drag y');
        near(after.depth, before.depth, 'drag must preserve camera depth');
      }
    }
  }
});

test('fit brings asymmetric, panned graph nodes inside narrow and expanded viewports', () => {
  for (const [width, height] of [[220, 280], [390, 620], [1080, 420], [1600, 900]]) {
    const c = camera(width, height);
    Object.assign(c.graphView, { zoom: 4, panX: 1900, panY: -2000 });
    c.graphView.nodes = [
      { key: 'left', point: { x: -450, y: -170, z: -180 } },
      { key: 'right', point: { x: 460, y: 170, z: 240 } },
      { key: 'middle', point: { x: 240, y: -50, z: 0 } },
    ];
    c.fitGraph();
    for (const node of c.graphView.nodes) {
      const point = c.projectGraphPoint(node.point);
      assert.ok(point.x - point.radius >= 0 && point.x + point.radius <= width, 'horizontal fit');
      assert.ok(point.y - point.radius >= 0 && point.y + point.radius <= height, 'vertical fit');
    }
  }
});

test('viewport resize preserves zoom and relative camera framing, and a round trip restores it', () => {
  const c = camera(720, 420);
  Object.assign(c.graphView, { zoom: 2.1, panX: 119, panY: -67 });
  const point = { x: 70, y: -90, z: 20 };
  const initial = c.projectGraphPoint(point);
  const scale = c.graphViewportScale();
  const svg = c.document.getElementById('graph-svg');
  Object.assign(svg, { clientWidth: 1300, clientHeight: 820 });
  c.resizeGraphViewport();
  const expanded = c.projectGraphPoint(point);
  const ratio = c.graphViewportScale() / scale;
  near(expanded.x - 650, (initial.x - 360) * ratio, 'relative x');
  near(expanded.y - 410, (initial.y - 210) * ratio, 'relative y');
  near(c.graphView.zoom, 2.1, 'user zoom');
  Object.assign(svg, { clientWidth: 720, clientHeight: 420 });
  c.resizeGraphViewport();
  const restored = c.projectGraphPoint(point);
  near(restored.x, initial.x, 'restored x');
  near(restored.y, initial.y, 'restored y');
});

test('a fitted graph remains visible when resizing between portrait and landscape', () => {
  const c = camera(320, 900);
  Object.assign(c.graphView, { yaw: 0, pitch: 0 });
  c.graphView.nodes = [
    { key: 'top', point: { x: -70, y: -175, z: 0 } },
    { key: 'bottom', point: { x: 70, y: 175, z: 0 } },
  ];
  c.fitGraph();
  const svg = c.document.getElementById('graph-svg');
  for (const [width, height] of [[1600, 280], [220, 1000], [1280, 760], [320, 900]]) {
    Object.assign(svg, { clientWidth: width, clientHeight: height });
    c.resizeGraphViewport();
    assert.equal(c.graphView.fitted, true);
    for (const node of c.graphView.nodes) {
      const point = c.projectGraphPoint(node.point);
      assert.ok(point.x - point.radius >= 0 && point.x + point.radius <= width, 'refitted x');
      assert.ok(point.y - point.radius >= 0 && point.y + point.radius <= height, 'refitted y');
    }
  }
  c.setGraphZoom(1.6);
  assert.equal(c.graphView.fitted, false, 'manual zoom exits automatic fit');
  Object.assign(svg, { clientWidth: 500, clientHeight: 500 });
  c.resizeGraphViewport();
  near(c.graphView.zoom, 1.6, 'manual zoom remains selected on resize');
});

test('orbit started after fitting continues through snapshot refresh and advances the camera', () => {
  const c = camera();
  const callbacks = new Map();
  let nextFrame = 0;
  Object.assign(c, {
    window: {
      requestAnimationFrame(callback) { callbacks.set(++nextFrame, callback); return nextFrame; },
      cancelAnimationFrame(frame) { callbacks.delete(frame); },
    },
    syncGraphPhysics() { return false; },
    renderForceControls() {},
  });
  c.graphView.nodes = [
    { key: 'left', category: 'work', point: { x: -300, y: -100, z: 0 } },
    { key: 'right', category: 'work', point: { x: 300, y: 100, z: 0 } },
  ];
  c.graphView.visibleCategories = new Set(['work']);
  c.state = { snapshot: { graph_nodes: c.graphView.nodes, graph_edges: [] }, selectedGraphKey: null };
  c.fitGraph();
  const fittedZoom = c.graphView.zoom;
  c.toggleGraphOrbit();
  const orbitFrame = c.graphView.frame;

  c.updateGraphVisibility(); // The same projection update used by automatic refresh.

  assert.equal(c.graphView.orbit, true, 'refresh must not stop the requested orbit');
  assert.equal(c.graphView.fitted, false, 'starting orbit exits automatic fit');
  assert.equal(c.graphView.frame, orbitFrame, 'refresh retains the animation frame');
  assert.equal(c.document.getElementById('graph-orbit').attributes['aria-pressed'], 'true');
  near(c.graphView.zoom, fittedZoom, 'orbit keeps the fitted starting zoom');
  callbacks.get(orbitFrame)(10);
  const initialYaw = c.graphView.yaw;
  callbacks.get(c.graphView.frame)(30);
  assert.ok(c.graphView.yaw > initialYaw, 'scheduled orbit continues moving the camera');
});

test('resizing updates both visible height and accessible value without changing the camera', () => {
  const c = camera();
  Object.assign(c.graphView, { zoom: 1.8, panX: 94, panY: -11 });
  for (const [requested, expected] of [[20, 280], [573.8, 574], [5000, 1200]]) {
    c.setGraphHeight(requested);
    assert.equal(c.document.getElementById('topology').properties['--graph-height'], `${expected}px`);
    assert.equal(c.document.getElementById('graph-resize').attributes['aria-valuenow'], String(expected));
    assert.equal(c.document.getElementById('graph-resize').attributes['aria-valuetext'], `${expected} pixels`);
    near(c.graphView.zoom, 1.8, 'zoom survives height changes');
    near(c.graphView.panX, 94, 'pan survives height changes');
  }
});

function gestures() {
  const c = camera(320, 280);
  Object.assign(c.graphView, { pointers: new Map(), pinch: null, drag: null });
  c.graphView.nodes = [{ key: 'test-node', category: 'work', kind: 'Task', point: { x: 0, y: 0, z: 0 } }];
  c.state = { selectedGraphKey: null, snapshot: { graph_nodes: c.graphView.nodes, graph_edges: [] } };
  c.relaxations = [];
  c.movements = [];
  c.relaxGraph = (...args) => { c.relaxations.push(args); };
  c.moveGraphNode = (...args) => { c.movements.push(args); };
  c.graphClientDelta = (x, y) => ({ x, y });
  c.selectGraphNode = node => { c.state.selectedGraphKey = node.key; };
  c.flushPendingSnapshot = () => {};
  const listeners = new Map();
  const captures = new Set();
  const classes = new Set();
  const svg = c.document.getElementById('graph-svg');
  Object.assign(svg, {
    getScreenCTM: () => null,
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); },
    releasePointerCapture(id) { captures.delete(id); },
    classList: {
      add(...names) { names.forEach(name => classes.add(name)); },
      remove(...names) { names.forEach(name => classes.delete(name)); },
    },
    addEventListener(type, listener) { listeners.set(type, listener); },
  });
  const start = frontend.indexOf("    const graphSvg = document.getElementById('graph-svg');");
  const end = frontend.indexOf("    graphSvg.addEventListener('wheel'", start);
  vm.runInContext(frontend.slice(start, end), c);
  return {
    c, captures, classes,
    send(type, pointerId, x = 0, y = 0, nodeKey = null) {
      listeners.get(type)({
        type, pointerId, clientX: x, clientY: y, button: 0, shiftKey: false,
        target: { closest() { return nodeKey ? { dataset: { nodeKey } } : null; } },
        preventDefault() {},
      });
    },
  };
}

test('pointer cancellation releases capture and does not select a dragged node', () => {
  const g = gestures();
  g.send('pointerdown', 1, 10, 10, 'test-node');
  assert.equal(g.c.graphView.drag.mode, 'node');
  g.send('pointercancel', 1);
  g.send('lostpointercapture', 1);
  assert.equal(g.c.graphView.drag, null);
  assert.equal(g.c.graphView.pointers.size, 0);
  assert.equal(g.c.state.selectedGraphKey, null);
  assert.equal(g.captures.size, 0);
  assert.equal(g.classes.size, 0);
});

test('a node click, including small hand jitter, selects without moving neighbors', () => {
  for (const jitter of [0, 3]) {
    const g = gestures();
    g.c.graphView.fitted = true;
    g.send('pointerdown', 1, 10, 10, 'test-node');
    g.send('pointermove', 1, 10 + jitter, 10);
    g.send('pointerup', 1, 10 + jitter, 10, 'test-node');
    assert.equal(g.c.state.selectedGraphKey, 'test-node');
    assert.equal(g.c.relaxations.length, 0, 'clicking must never run physics iterations');
    assert.equal(g.c.movements.length, 0, 'sub-threshold motion must not reposition the node');
    assert.equal(g.c.graphView.fitted, true, 'a selection keeps existing camera framing');
  }
});

test('a deliberate node drag moves the node and relaxes connected work only after the threshold', () => {
  const g = gestures();
  g.send('pointerdown', 1, 10, 10, 'test-node');
  g.send('pointermove', 1, 12, 11);
  assert.equal(g.c.relaxations.length, 0);
  assert.equal(g.c.movements.length, 0);
  g.send('pointermove', 1, 40, 20);
  assert.ok(g.c.relaxations.length > 0);
  assert.ok(g.c.movements.length > 0);
  g.send('pointerup', 1, 40, 20, 'test-node');
  assert.equal(g.c.graphView.drag, null);
  assert.equal(g.captures.size, 0);
});

test('two-pointer pinch changes zoom and releases state when both captures end', () => {
  const g = gestures();
  g.send('pointerdown', 1, 100, 140);
  g.send('pointerdown', 2, 200, 140);
  g.send('pointermove', 2, 250, 140);
  near(g.c.graphView.zoom, 1.5, 'pinch zoom');
  assert.equal(g.c.graphView.drag, null);
  g.send('pointercancel', 1);
  g.send('lostpointercapture', 1);
  g.send('pointercancel', 2);
  g.send('lostpointercapture', 2);
  assert.equal(g.c.graphView.pinch, null);
  assert.equal(g.c.graphView.pointers.size, 0);
  assert.equal(g.captures.size, 0);
  assert.equal(g.classes.size, 0);
});

test('an ignored third pointer cannot cancel an active two-pointer pinch', () => {
  const g = gestures();
  g.send('pointerdown', 1, 100, 140);
  g.send('pointerdown', 2, 200, 140);
  const pinch = g.c.graphView.pinch;
  g.send('pointerdown', 3, 250, 140);
  g.send('pointercancel', 3);
  assert.equal(g.c.graphView.pinch, pinch);
  g.send('pointermove', 2, 250, 140);
  near(g.c.graphView.zoom, 1.5, 'pinch must continue');
});
