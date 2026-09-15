import {requireThat} from './contracts.mjs';
import {compileNetwork} from './network.mjs';

/** Exact integer maximum-weight closure via augmenting-path max flow. */
export function solveClosure(nodes, dependencies, scale = 1000) {
  const network = compileNetwork(nodes, dependencies, scale);
  const {C, M, weights} = network;
  const ordered = weights, source = ordered.length, sink = source + 1;
  const indices = new Map(ordered.map((n, i) => [n.key, i]));
  indices.set('$source', source); indices.set('$sink', sink);
  const graph = Array.from({length: sink + 1}, () => []), manifest = [];
  for (const arc of network.edges) {
    const from = indices.get(arc.from), to = indices.get(arc.to);
    const forward = {to, residual: arc.capacity, reverse: graph[to].length};
    const backward = {to: from, residual: 0, reverse: graph[from].length};
    graph[from].push(forward); graph[to].push(backward);
    manifest.push({arc, from, to, forward});
  }
  let flow = 0, reachable;
  for (;;) {
    const parent = Array(graph.length).fill(null), queue = [source]; parent[source] = [-1, -1];
    for (let q = 0; q < queue.length && parent[sink] === null; q++) {
      const from = queue[q];
      graph[from].forEach((e, i) => { if (e.residual > 0 && parent[e.to] === null) { parent[e.to] = [from, i]; queue.push(e.to); } });
    }
    if (parent[sink] === null) { reachable = new Set(queue); break; }
    let amount = Infinity;
    for (let at = sink; at !== source; at = parent[at][0]) { const [from, i] = parent[at]; amount = Math.min(amount, graph[from][i].residual); }
    for (let at = sink; at !== source; at = parent[at][0]) { const [from, i] = parent[at], e = graph[from][i]; e.residual -= amount; graph[at][e.reverse].residual += amount; }
    flow += amount;
  }
  const selected = ordered.filter((_, i) => reachable.has(i)).map(n => n.key);
  const network_edges = manifest.map(e => ({...e.arc, flow: e.arc.capacity - e.forward.residual, cut: reachable.has(e.from) && !reachable.has(e.to)}));
  const cut = network_edges.filter(e => e.cut).map(({from, to, capacity, type}) => ({from, to, capacity, type}));
  const cut_capacity = cut.reduce((sum, e) => sum + e.capacity, 0);
  requireThat(cut_capacity === flow && !cut.some(e => e.type === 'prerequisite'), 'solver_certificate', 'Invalid cut certificate.');
  return {selected, quantized_utility: (C - flow) / scale, C, M, cut_capacity, scale, cut, weights, network_edges, solver: 'augmenting_path_v1', tie_policy: 'minimal_source_side', rounding: 'nearest_ties_toward_positive_infinity'};
}
