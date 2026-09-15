/** Deterministic dependency columns; SCCs keep cyclic bundles together. */
export const NODE_WIDTH = 250, NODE_HEIGHT = 252;
export function layoutFlow(nodes, dependencies) {
  const keys = nodes.map(n => n.key).sort(), known = new Set(keys);
  const forward = new Map(keys.map(k => [k, []])), backward = new Map(keys.map(k => [k, []]));
  for (const e of dependencies) if (known.has(e.dependent) && known.has(e.prerequisite)) {
    forward.get(e.dependent).push(e.prerequisite); backward.get(e.prerequisite).push(e.dependent);
  }
  const visited = new Set(), order = [];
  for (const root of keys) {
    if (visited.has(root)) continue;
    const stack = [[root, false]];
    while (stack.length) {
      const [key, exit] = stack.pop();
      if (exit) { order.push(key); continue; }
      if (visited.has(key)) continue; visited.add(key); stack.push([key, true]);
      for (const next of forward.get(key)) if (!visited.has(next)) stack.push([next, false]);
    }
  }
  const groups = [], groupOf = new Map();
  for (const root of order.reverse()) {
    if (groupOf.has(root)) continue;
    const id = groups.length, group = [], stack = [root]; groupOf.set(root, id);
    while (stack.length) {
      const key = stack.pop(); group.push(key);
      for (const next of backward.get(key)) if (!groupOf.has(next)) { groupOf.set(next, id); stack.push(next); }
    }
    groups.push(group.sort());
  }
  const outgoing = groups.map(() => new Set()), indegrees = groups.map(() => 0), level = groups.map(() => 0);
  for (const [from, targets] of forward) for (const to of targets) {
    const a = groupOf.get(from), b = groupOf.get(to);
    if (a !== b && !outgoing[a].has(b)) { outgoing[a].add(b); indegrees[b]++; }
  }
  const queue = indegrees.flatMap((n, i) => n === 0 ? [i] : []);
  for (let at = 0; at < queue.length; at++) for (const next of outgoing[queue[at]]) {
    level[next] = Math.max(level[next], level[queue[at]] + 1);
    if (--indegrees[next] === 0) queue.push(next);
  }
  const columns = new Map();
  for (const key of keys) {
    const depth = level[groupOf.get(key)]; if (!columns.has(depth)) columns.set(depth, []); columns.get(depth).push(key);
  }
  const maxRows = Math.max(1, ...[...columns.values()].map(c => c.length));
  const height = Math.max(580, 110 + maxRows * (NODE_HEIGHT + 48));
  const maxLevel = Math.max(0, ...columns.keys()), width = Math.max(1000, 520 + (maxLevel + 1) * (NODE_WIDTH + 140));
  const positions = new Map();
  for (const [column, items] of columns) {
    const start = (height - (items.length * NODE_HEIGHT + (items.length - 1) * 48)) / 2;
    items.forEach((key, row) => positions.set(key, {x: 215 + column * (NODE_WIDTH + 140), y: start + row * (NODE_HEIGHT + 48), width: NODE_WIDTH, height: NODE_HEIGHT}));
  }
  positions.set('$source', {x: 30, y: height / 2 - 62, width: 116, height: 124});
  positions.set('$sink', {x: width - 145, y: height / 2 - 62, width: 116, height: 124});
  return {positions, width, height};
}
export function edgeGeometry(from, to) {
  const x1 = from.x + from.width, y1 = from.y + from.height / 2;
  const x2 = to.x, y2 = to.y + to.height / 2;
  if (x2 > x1) {
    const bend = Math.max(50, (x2 - x1) / 2);
    return {path: 'M ' + x1 + ' ' + y1 + ' C ' + (x1 + bend) + ' ' + y1 + ', ' + (x2 - bend) + ' ' + y2 + ', ' + x2 + ' ' + y2, x: (x1 + x2) / 2, y: (y1 + y2) / 2};
  }
  const bend = Math.max(x1, x2 + to.width) + 75;
  return {path: 'M ' + x1 + ' ' + y1 + ' C ' + bend + ' ' + y1 + ', ' + bend + ' ' + y2 + ', ' + (x2 + to.width) + ' ' + y2, x: bend - 10, y: (y1 + y2) / 2};
}
