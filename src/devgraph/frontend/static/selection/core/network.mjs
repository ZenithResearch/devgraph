import {compare, finite, requireThat} from './contracts.mjs';

/** Compile the network once for both the visual editor and the exact solver. */
export function compileNetwork(nodes, dependencies, scale = 1000) {
  requireThat(nodes.length <= 3000 && dependencies.length <= 30000, 'network_size', 'This solver supports at most 3,000 work nodes and 30,000 dependencies.');
  requireThat(Number.isSafeInteger(scale) && scale > 0 && scale <= 1e6, 'quantization', 'Invalid quantization.');
  const ordered = [...nodes].sort((a, b) => compare(a.key, b.key));
  const indices = new Map(ordered.map((n, i) => [n.key, i]));
  requireThat(ordered.every(n => typeof n.key === "string" && !["$source", "$sink"].includes(n.key)), "node_key", "Work keys must not use reserved terminal identities.");
  requireThat(indices.size === nodes.length, 'duplicate_node', 'Duplicate Work key.');
  const weights = ordered.map(n => { finite(n.utility, 'Utility'); const v = Math.round(n.utility * scale); requireThat(Number.isSafeInteger(v), 'overflow', 'Utility exceeds safe integer capacity.'); return v; });
  // Keep the sum of all absolute capacities plus M safely representable.
  const absolute = weights.reduce((s, v) => s + Math.abs(v), 0);
  requireThat(Number.isSafeInteger(absolute) && absolute < Number.MAX_SAFE_INTEGER / 4, 'overflow', 'Network exceeds safe integer capacity.');
  const C = weights.reduce((sum, w) => sum + Math.max(w, 0), 0), M = C + 1;
  const edges = [];
  function add(from, to, capacity, type) {
    if (from !== to) edges.push({id: JSON.stringify([from, to, type, edges.length]), from, to, capacity, type});
  }
  weights.forEach((w, i) => {
    if (w > 0) add('$source', ordered[i].key, w, 'benefit');
    if (w < 0) add(ordered[i].key, '$sink', -w, 'cost');
  });
  for (const d of [...dependencies].sort((a, b) => compare(a.dependent + '\0' + a.prerequisite, b.dependent + '\0' + b.prerequisite))) {
    requireThat(indices.has(d.dependent) && indices.has(d.prerequisite), 'missing_dependency', 'A prerequisite is outside the network.');
    add(d.dependent, d.prerequisite, M, 'prerequisite');
  }
  return {C, M, scale, weights: ordered.map((n, i) => ({key: n.key, weight: weights[i]})), edges};
}
