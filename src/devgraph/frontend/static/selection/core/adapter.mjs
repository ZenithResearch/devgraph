import {compare, requireThat, validateProfile} from './contracts.mjs';
const KINDS = ['Proposal', 'Initiative', 'Project', 'Issue', 'Task'];

export function fromMonitorSnapshot(raw) {
  requireThat(Array.isArray(raw?.graph_nodes) && Array.isArray(raw.graph_edges), 'snapshot', 'Expected a Monitor snapshot.');
  for (const n of raw.graph_nodes) {
    requireThat(n.version == null || typeof n.version === 'string' || Number.isSafeInteger(n.version), 'node_version', 'Unsafe numeric Work version; use a decimal string.');
  }
  const nodes = raw.graph_nodes.filter(n => n.category === 'work' && KINDS.includes(n.kind)).map(n => ({key: n.key, id: n.id, kind: n.kind, title: n.title, version: n.version == null ? null : String(n.version), status: n.status}));
  const keys = new Set(nodes.map(n => n.key)), edges = raw.graph_edges;
  const coverage = raw.selection_scope?.schema === 'devgraph.selection-scope.v1' ? {...raw.selection_scope, status: raw.selection_scope.edge_scan === 'all_stored_edges' ? 'scanned' : 'unknown'} : {status: 'unknown', consistency: 'assembled', read_started_at: raw.generated_at, read_finished_at: raw.generated_at, unresolved: []};
  return {schema: 'devgraph.selection-snapshot.v1', nodes,
    dependencies: edges.filter(e => e.relationship === 'DEPENDS_ON' && keys.has(e.source)).map(e => ({dependent: e.source, prerequisite: e.target})),
    blocks: edges.filter(e => e.relationship === 'BLOCKS' && keys.has(e.target)).map(e => ({dependent: e.target, prerequisite: e.source})),
    hierarchy: edges.filter(e => e.relationship === 'HAS_CHILD').map(e => ({parent: e.source, child: e.target})), coverage};
}
export function validateSnapshot(s) {
  requireThat(s?.schema === 'devgraph.selection-snapshot.v1', 'snapshot_schema', 'Unsupported selection snapshot version.');
  for (const field of ['nodes', 'dependencies', 'blocks', 'hierarchy']) requireThat(Array.isArray(s[field]), 'snapshot', 'Missing snapshot ' + field + '.');
  requireThat(s.nodes.length <= 3000 && s.dependencies.length + s.blocks.length + s.hierarchy.length <= 30000, 'snapshot_size', 'Snapshot exceeds the supported selection size.');
  const keys = new Set();
  for (const n of s.nodes) {
    requireThat(typeof n.key === 'string' && n.key.length <= 512 && KINDS.includes(n.kind) && typeof n.id === 'string' && n.key === n.kind + ':' + n.id, 'node_identity', 'Invalid canonical Work identity.');
    requireThat(!keys.has(n.key), 'duplicate_node', 'Duplicate Work key: ' + n.key); keys.add(n.key);
    requireThat(typeof n.title === 'string' && n.title.length <= 10000, 'node_title', 'Invalid Work title.');
    requireThat(n.version === null || typeof n.version === 'string' && /^[1-9][0-9]{0,18}$/.test(n.version) && BigInt(n.version) <= 9223372036854775807n, 'node_version', 'Work version must be a canonical decimal string.');
  }
  for (const e of [...s.dependencies, ...s.blocks]) requireThat(typeof e.dependent === 'string' && typeof e.prerequisite === 'string', 'dependency', 'Invalid prerequisite edge.');
  for (const e of s.hierarchy) requireThat(typeof e.parent === 'string' && typeof e.child === 'string', 'hierarchy', 'Invalid hierarchy edge.');
  requireThat(s.coverage && ['scanned', 'unknown'].includes(s.coverage.status) && ['assembled', 'fixture'].includes(s.coverage.consistency) && Array.isArray(s.coverage.unresolved), 'coverage', 'Snapshot coverage metadata is required.');
  for (const key of ['read_started_at', 'read_finished_at']) requireThat(typeof s.coverage[key] === 'string' && (s.coverage.status === 'unknown' || Number.isFinite(Date.parse(s.coverage[key]))), 'coverage_time', 'Snapshot read window must contain valid timestamp strings.');
  if (s.coverage.status === 'scanned') requireThat(Date.parse(s.coverage.read_started_at) <= Date.parse(s.coverage.read_finished_at), 'coverage_time', 'Snapshot read window is reversed.');
  for (const e of s.coverage.unresolved) requireThat(typeof e.work_key === 'string' && typeof e.reason === 'string', 'coverage', 'Invalid unresolved obligation.');
  return s;
}
export function resolveScope(snapshot, seeds, profile) {
  validateSnapshot(snapshot); validateProfile(profile);
  requireThat(Array.isArray(seeds) && seeds.length > 0 && seeds.length <= 3000 && new Set(seeds).size === seeds.length, 'scope_empty', 'Choose at least one unique candidate.');
  requireThat(snapshot.coverage.status === 'scanned', 'coverage_unknown', 'Dependency coverage is unknown. Load a current Monitor snapshot or a declared scenario.');
  const nodes = new Map(snapshot.nodes.map(n => [n.key, n]));
  const edges = [...snapshot.dependencies, ...(profile.blocks_policy === 'require' ? snapshot.blocks : [])];
  const required = new Map();
  for (const e of edges) { if (!required.has(e.dependent)) required.set(e.dependent, []); required.get(e.dependent).push(e.prerequisite); }
  const queue = [...seeds], keys = new Set(seeds);
  for (let k = 0; k < queue.length; k++) {
    const key = queue[k], node = nodes.get(key);
    requireThat(node, 'missing_dependency', 'Required work is unavailable: ' + key);
    requireThat(node.version !== null, 'missing_version', 'Work version is unavailable: ' + node.title);
    const gap = snapshot.coverage.unresolved.find(x => x.work_key === key);
    requireThat(!gap, 'unresolved_dependency', 'Unresolved obligation for ' + node.title + (gap ? ': ' + gap.reason : '.'));
    requireThat(profile.blocks_policy === 'require' || !snapshot.blocks.some(e => e.dependent === key), 'unresolved_blocker', 'Classify blockers before selecting ' + node.title + '.');
    for (const dep of required.get(key) || []) if (!keys.has(dep)) { keys.add(dep); queue.push(dep); }
  }
  // Walk all containment paths, even when intermediate nodes are not selected.
  const children = new Map();
  for (const e of snapshot.hierarchy) { if (!children.has(e.parent)) children.set(e.parent, []); children.get(e.parent).push(e.child); }
  for (const parent of keys) {
    const seen = new Set(), pending = [...(children.get(parent) || [])];
    for (let k = 0; k < pending.length; k++) {
      const child = pending[k]; if (seen.has(child)) continue; seen.add(child);
      requireThat(!keys.has(child), 'overlapping_scope', 'Choose one granularity: ' + parent + ' and ' + child + ' overlap.');
      pending.push(...(children.get(child) || []));
    }
  }
  const unique = new Map(edges.filter(e => keys.has(e.dependent)).map(e => [e.dependent + '\0' + e.prerequisite, e]));
  return {nodes: [...keys].sort(compare).map(k => nodes.get(k)), dependencies: [...unique.values()], added: [...keys].filter(k => !seeds.includes(k)).sort(compare)};
}
