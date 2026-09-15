import {bindings, describeNetwork} from '../core/index.mjs';
import {el, format} from './components.mjs';
import {layoutFlow, edgeGeometry} from './flow-layout.mjs';
const svg = (name, attributes = {}) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
};

/** DOM-only network editor; selection mathematics stays in the shared core. */
export class FlowView {
  constructor(host, callbacks) {
    this.host = host; this.callbacks = callbacks; this.cards = new Map(); this.positions = new Map(); this.zoom = 1; this.pan = {x: 0, y: 0}; this.signature = ''; this.data = null; this.activeKey = null;
    this.world = el('div', undefined, 'flow-world');
    this.lines = svg('svg', {'class': 'flow-lines', 'aria-hidden': 'true'});
    const defs = svg('defs');
    for (const [name, color] of [['normal', '#698c7c'], ['flow', '#7cf7cf'], ['cut', '#f6c86f']]) {
      const marker = svg('marker', {id: 'arrow-' + name, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: 'auto', markerUnits: 'userSpaceOnUse'});
      marker.append(svg('path', {d: 'M0,0 L8,4 L0,8 Z', fill: color})); defs.append(marker);
    }
    this.arcs = svg('g'); this.lines.append(defs, this.arcs); this.world.append(this.lines); host.append(this.world);
    this.source = this.terminal('$source', 's', 'Source'); this.sink = this.terminal('$sink', 't', 'Sink');
    host.addEventListener('focusin', event => {
      const card = event.target.closest('[data-node-key]'); if (!card) return;
      const box = card.getBoundingClientRect(), viewport = host.getBoundingClientRect();
      if (box.left < viewport.left || box.right > viewport.right || box.top < viewport.top || box.bottom > viewport.bottom) this.focusNode(card.dataset.nodeKey);
    });
    host.addEventListener('pointerdown', event => this.pointerDown(event));
    host.addEventListener('pointermove', event => this.pointerMove(event));
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) host.addEventListener(name, event => this.pointerEnd(event));
    host.addEventListener('wheel', event => { if (event.ctrlKey || event.metaKey) { event.preventDefault(); this.setZoom(this.zoom * Math.exp(-event.deltaY * .008), event.clientX, event.clientY); } }, {passive: false});
    host.addEventListener('keydown', event => {
      const key = event.target.closest('[data-drag-key]')?.dataset.dragKey;
      if (key && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault(); const p = this.positions.get(key), distance = event.shiftKey ? 40 : 10;
        const offset = {ArrowLeft: [-distance, 0], ArrowRight: [distance, 0], ArrowUp: [0, -distance], ArrowDown: [0, distance]}[event.key];
        p.x += offset[0]; p.y += offset[1]; this.place(); this.drawEdges(); return;
      }
      if (event.target !== host) return;
      if (event.key.toLowerCase() === 'f') { event.preventDefault(); this.fit(); }
      if (event.key === '+' || event.key === '=') { event.preventDefault(); this.setZoom(this.zoom * 1.15); }
      if (event.key === '-') { event.preventDefault(); this.setZoom(this.zoom / 1.15); }
    });
    this.resize = new ResizeObserver(() => { if (!this.initialFit && this.data?.nodes.length) this.fit(); }); this.resize.observe(host);
  }
  terminal(key, symbol, title) {
    const node = el('div', undefined, 'flow-terminal'); node.dataset.terminal = key;
    node.append(el('strong', symbol), el('span', title), el('small', '—')); this.world.append(node); return node;
  }
  makeCard(node, definitions) {
    const card = el('article', undefined, 'flow-node'); card.dataset.nodeKey = node.key; card.setAttribute('aria-label', node.title);
    const head = el('div', undefined, 'node-top'), drag = el('button', '⠿', 'node-drag-handle'); drag.dataset.dragKey = node.key; drag.setAttribute('aria-label', 'Move ' + node.title); drag.title = 'Drag to move · arrow keys move when focused';
    const title = el('button', node.title, 'node-title'); title.onclick = () => this.callbacks.inspect(node.key);
    head.append(drag, title); const meta = el('div', node.kind + ' · v' + (node.version ?? '?'), 'node-meta');
    const utility = el('div', undefined, 'node-utility'); utility.append(el('span', 'u(i)'), el('strong', '—'));
    const fields = el('div', undefined, 'node-inputs');
    for (const b of definitions.slice(0, 2)) {
      const label = el('label', (b.key === 'effort__estimate' ? 'Effort' : (b.label || b.id)) + ' · ' + b.unit);
      const input = el('input'); input.type = 'number'; input.step = 'any'; input.placeholder = 'Unknown'; input.dataset.predictor = b.key;
      input.setAttribute('aria-label', (b.label || b.id) + ' for ' + node.title);
      if (b.channel === 'cost') input.min = '0';
      input.addEventListener('input', () => this.callbacks.edit(node.key, b.key, 'point', input.value === '' ? null : Number(input.value)));
      label.append(input); fields.append(label);
    }
    const status = el('div', undefined, 'node-status');
    const foot = el('div', undefined, 'node-foot'), seedLabel = el('label', undefined, 'node-scope'), seed = el('input'); seed.type = 'checkbox'; seed.setAttribute('aria-label', 'Candidate scope: ' + node.title);
    seed.onchange = () => this.callbacks.seed(node.key, seed.checked); seedLabel.append(seed, el('span', 'Candidate'));
    const inspect = el('button', 'Inspect →', 'node-inspect'); inspect.setAttribute('aria-label', 'Inspect ' + node.title); inspect.onclick = () => this.callbacks.inspect(node.key); foot.append(seedLabel, inspect);
    card.append(head, meta, utility, fields, status, foot); this.world.append(card);
    return {card, title, meta, utility: utility.lastChild, inputs: [...fields.querySelectorAll('input')], status, seed, scopeLabel: seedLabel.lastChild, inspect};
  }
  update(data) {
    this.data = data; this.world.hidden = !data.nodes.length;
    const all = bindings(data.profile), primary = [all.find(b => b.channel === 'benefit'), all.find(b => b.channel === 'cost')].filter(Boolean);
    const definitionKey = primary.map(b => b.key + ':' + b.unit).join('|');
    const signature = JSON.stringify([data.nodes.map(n => n.key).sort(), data.dependencies]);
    if (signature !== this.signature) {
      const layout = layoutFlow(data.nodes, data.dependencies); this.positions = layout.positions; this.width = layout.width; this.height = layout.height; this.signature = signature; this.initialFit = false;
    }
    for (const [key, entry] of this.cards) if (!data.nodes.some(n => n.key === key) || entry.definitionKey !== definitionKey) { entry.card.remove(); this.cards.delete(key); }
    for (const node of data.nodes) {
      if (!this.cards.has(node.key)) this.cards.set(node.key, {...this.makeCard(node, primary), definitionKey});
      const entry = this.cards.get(node.key), score = data.scores.get(node.key), problem = data.problems.get(node.key), result = data.run?.rows.find(r => r.key === node.key);
      const required = data.added.includes(node.key), inScope = data.scopeKeys.has(node.key), review = data.needsReview(node.key), matches = !data.search || (node.title + ' ' + node.key).toLowerCase().includes(data.search);
      entry.card.classList.toggle('is-selected', !!result?.selected); entry.card.classList.toggle('is-inspected', this.activeKey === node.key);
      entry.card.classList.toggle('is-incomplete', !score); entry.card.classList.toggle('is-outside', !inScope); entry.card.classList.toggle('is-dimmed', !matches);
      entry.card.dataset.utility = Number.isFinite(score?.utility) ? String(score.utility) : 'unknown';
      entry.title.textContent = node.title; entry.title.title = node.title; entry.card.setAttribute('aria-label', node.title); entry.meta.textContent = node.kind + ' · v' + (node.version ?? '?') + (required ? ' · prerequisite' : '');
      entry.utility.textContent = format(score?.utility); entry.utility.className = score ? score.utility < 0 ? 'negative' : 'positive' : 'warning';
      for (const input of entry.inputs) {
        const value = data.observations[node.key]?.values?.[input.dataset.predictor]?.point ?? '';
        if (document.activeElement !== input && input.value !== String(value)) input.value = value;
        input.disabled = data.loading || review; input.title = review ? 'Review the new Work version or forecast context in the inspector.' : 'Updates this local estimate immediately';
      }
      entry.seed.checked = data.seeds.includes(node.key) || required; entry.seed.disabled = required || data.loading; entry.scopeLabel.textContent = required ? 'Required' : 'Candidate';
      entry.status.textContent = review ? 'Review version / context →' : problem ? 'Inputs needed · open variables →' : !inScope ? 'Outside decision scope' : result ? result.selected ? 'Selected' + (result.required_by.length ? ' · prerequisite' : '') : 'Not selected in this run' : 'Ready · ' + format(score.benefit) + ' − ' + format(score.burden);
      entry.status.title = problem?.details?.map(p => p.message).join(' ') || problem?.message || '';
      entry.inspect.textContent = review ? 'Review →' : all.length > 2 ? '+' + (all.length - 2) + ' inputs →' : 'Inspect →';
    }
    this.network = null;
    try {
      if (data.scopeKeys.size) this.network = describeNetwork({nodes: data.nodes.filter(n => data.scopeKeys.has(n.key)), dependencies: data.dependencies.filter(e => data.scopeKeys.has(e.dependent) && data.scopeKeys.has(e.prerequisite)), profile: data.profile, observations: data.observations});
    } catch (error) {
      this.callbacks.networkError(error);
      // Keep the structural network visible if numeric capacities overflow.
      try { this.network = describeNetwork({nodes: data.nodes.filter(n => data.scopeKeys.has(n.key)), dependencies: data.dependencies.filter(e => data.scopeKeys.has(e.dependent) && data.scopeKeys.has(e.prerequisite)), profile: data.profile}); } catch {}
    }
    this.source.querySelector('small').textContent = this.network?.C != null ? 'C = ' + this.capacity(this.network.C) : 'C = ?';
    this.sink.querySelector('small').textContent = data.run ? 'flow = ' + this.capacity(data.run.solution.cut_capacity) : 'flow = —';
    this.source.classList.toggle('is-selected', !!data.run);
    this.place(); this.drawEdges(); this.transform();
    if (!this.initialFit && data.nodes.length && this.host.clientWidth > 0) { this.fit(); if (this.zoom < .65) this.focusNode(data.nodes[0].key); }
    const total = data.run ? ' · max flow ' + this.capacity(data.run.solution.cut_capacity) : '';
    this.callbacks.summary(!data.nodes.length ? 'Load Work objects to initialize the network' : !data.scopeKeys.size ? 'Choose candidate nodes to define the decision network' : this.network?.C != null ? 'C ' + this.capacity(this.network.C) + ' · M ' + this.capacity(this.network.M) + total : 'Network loaded · ? = unresolved capacity · fill variables on the nodes');
  }
  capacity(value) { return format(value / this.data.profile.quantization); }
  place() {
    for (const [key, entry] of this.cards) {
      const position = this.positions.get(key); if (!position) continue;
      entry.card.style.left = position.x + 'px'; entry.card.style.top = position.y + 'px';
    }
    for (const [key, terminal] of [['$source', this.source], ['$sink', this.sink]]) {
      const p = this.positions.get(key); if (!p) continue; terminal.style.left = p.x + 'px'; terminal.style.top = p.y + 'px';
    }
    this.world.style.width = (this.width || 1000) + 'px'; this.world.style.height = (this.height || 580) + 'px';
    this.lines.setAttribute('width', this.width || 1000); this.lines.setAttribute('height', this.height || 580);
  }
  drawEdges() {
    if (!this.data) return; this.arcs.replaceChildren();
    const edges = this.data.run?.solution.network_edges || this.network?.edges || this.data.dependencies.map((e, i) => ({id: 'pending-' + i, from: e.dependent, to: e.prerequisite, type: 'prerequisite', capacity: null}));
    for (const edge of edges) {
      const from = this.positions.get(edge.from), to = this.positions.get(edge.to); if (!from || !to) continue;
      const geometry = edgeGeometry(from, to), kind = edge.cut ? 'cut' : edge.flow > 0 ? 'flow' : 'normal';
      const group = svg('g', {'class': 'flow-edge ' + kind + (edge.capacity === null ? ' pending' : '') + (edge.type === 'prerequisite' ? ' hard' : ''), 'data-edge-from': edge.from, 'data-edge-to': edge.to});
      const line = svg('path', {d: geometry.path, 'marker-end': 'url(#arrow-' + kind + ')'});
      const label = svg('text', {x: geometry.x, y: geometry.y - 9, 'text-anchor': 'middle'});
      const capacity = edge.capacity === null ? (edge.type === 'prerequisite' ? '?' : 'u?') : this.capacity(edge.capacity);
      label.textContent = (edge.flow !== undefined ? this.capacity(edge.flow) + ' / ' : '') + capacity + (edge.type === 'prerequisite' ? ' · M' : '') + (edge.cut ? ' · cut' : '');
      const title = svg('title'); title.textContent = edge.from + ' → ' + edge.to + '; ' + (edge.type === 'prerequisite' ? 'hard prerequisite' : 'net utility capacity') + '; ' + (edge.expression || '') + '; ' + label.textContent;
      group.append(title, line, label); this.arcs.append(group);
    }
  }
  focusNode(key) { const p = this.positions.get(key); if (!p) return; this.zoom = Math.max(.7, Math.min(1, this.host.clientWidth / (p.width + 40))); this.pan = {x: (this.host.clientWidth - p.width * this.zoom) / 2 - p.x * this.zoom, y: (this.host.clientHeight - p.height * this.zoom) / 2 - p.y * this.zoom}; this.initialFit = true; this.transform(); }
  select(key) { this.activeKey = key; for (const [k, e] of this.cards) e.card.classList.toggle('is-inspected', k === key); }
  fit() {
    if (!this.positions.size) return;
    const all = [...this.positions.values()], minX = Math.min(...all.map(p => p.x)) - 24, minY = Math.min(...all.map(p => p.y)) - 24;
    const maxX = Math.max(...all.map(p => p.x + p.width)) + 24, maxY = Math.max(...all.map(p => p.y + p.height)) + 24;
    this.zoom = Math.min(1, Math.max(.04, Math.min(this.host.clientWidth / (maxX - minX), this.host.clientHeight / (maxY - minY))));
    this.pan = {x: (this.host.clientWidth - (maxX - minX) * this.zoom) / 2 - minX * this.zoom, y: (this.host.clientHeight - (maxY - minY) * this.zoom) / 2 - minY * this.zoom};
    this.initialFit = true; this.transform();
  }
  setZoom(value, clientX, clientY) {
    const rect = this.host.getBoundingClientRect(), point = {x: clientX == null ? rect.width / 2 : clientX - rect.left, y: clientY == null ? rect.height / 2 : clientY - rect.top};
    const next = Math.max(.04, Math.min(2, value)), ratio = next / this.zoom;
    this.pan.x = point.x - (point.x - this.pan.x) * ratio; this.pan.y = point.y - (point.y - this.pan.y) * ratio; this.zoom = next; this.transform();
  }
  transform() { this.world.style.transform = 'translate(' + this.pan.x + 'px,' + this.pan.y + 'px) scale(' + this.zoom + ')'; this.callbacks.zoom(Math.round(this.zoom * 100)); }
  pointerDown(event) {
    if (event.button !== 0 || this.drag) return;
    const key = event.target.closest('[data-drag-key]')?.dataset.dragKey;
    if (!key && event.target.closest('article,button,input,label,.flow-terminal')) return;
    event.preventDefault(); this.drag = {id: event.pointerId, key, x: event.clientX, y: event.clientY}; this.host.setPointerCapture(event.pointerId); this.host.classList.add('dragging');
  }
  pointerMove(event) {
    if (this.drag?.id !== event.pointerId) return;
    const dx = event.clientX - this.drag.x, dy = event.clientY - this.drag.y; this.drag.x = event.clientX; this.drag.y = event.clientY;
    if (this.drag.key) { const p = this.positions.get(this.drag.key); p.x += dx / this.zoom; p.y += dy / this.zoom; this.place(); this.drawEdges(); }
    else { this.pan.x += dx; this.pan.y += dy; this.transform(); }
  }
  pointerEnd(event) { if (this.drag?.id !== event.pointerId) return; this.drag = null; this.host.classList.remove('dragging'); if (this.host.hasPointerCapture(event.pointerId)) this.host.releasePointerCapture(event.pointerId); }
}
