/** Versioned, JSON-only selection contracts. No browser or authority state. */
export class SelectionError extends Error {
  constructor(code, message, details = []) { super(message); this.name = 'SelectionError'; this.code = code; this.details = details; }
}
export function requireThat(condition, code, message) {
  if (!condition) throw new SelectionError(code, message);
}
export function finite(value, name, min = -Infinity) {
  requireThat(typeof value === 'number' && Number.isFinite(value) && value >= min, 'invalid_number', name + ' must be a finite number' + (min !== -Infinity ? ' ≥ ' + min : ''));
  return value;
}
export const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
export const clone = value => JSON.parse(canonicalJSON(value));
export function canonicalJSON(value, depth = 0) {
  requireThat(depth < 80, 'input_depth', 'Input nesting is too deep.');
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') { finite(value, 'JSON value'); return JSON.stringify(value); }
  if (Array.isArray(value)) return '[' + value.map(v => canonicalJSON(v, depth + 1)).join(',') + ']';
  requireThat(value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype, 'invalid_json', 'Inputs must be plain JSON records.');
  return '{' + Object.keys(value).sort(compare).map(k => JSON.stringify(k) + ':' + canonicalJSON(value[k], depth + 1)).join(',') + '}';
}
export async function fingerprint(value) {
  const bytes = new TextEncoder().encode(canonicalJSON(value));
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
}
function id(value, label) {
  requireThat(typeof value === 'string' && /^[a-z][a-z0-9_]{0,47}$/.test(value) && !value.includes('__'), 'invalid_id', label + ' must be a short lowercase identifier without double underscores.');
}
export function bindings(profile) {
  return [...profile.goals.flatMap(group => group.predictors.map(p => ({...p, key: group.id + '__' + p.id, group: group.id, channel: 'benefit'}))),
    ...profile.resources.flatMap(group => group.predictors.map(p => ({...p, key: group.id + '__' + p.id, group: group.id, channel: 'cost', direction: 1})))];
}
export function validateProfile(p) {
  requireThat(p?.schema === 'devgraph.selection-profile.v1', 'profile_schema', 'Unsupported decision profile version.');
  requireThat(typeof p.id === 'string' && p.id.length > 0 && typeof p.version === 'string' && p.version.length > 0, 'profile_identity', 'Profile ID and version are required.');
  for (const field of ['baseline', 'horizon', 'utility_unit']) requireThat(typeof p[field] === 'string' && p[field].trim().length > 0, 'profile_context', field + ' is required.');
  requireThat(['none', 'conservative_interpolation_v1'].includes(p.confidence_mode), 'confidence_mode', 'Unsupported confidence policy.');
  requireThat(['require', 'unresolved'].includes(p.blocks_policy), 'blocks_policy', 'Declare how BLOCKS relationships are interpreted.');
  requireThat(Number.isSafeInteger(p.quantization) && p.quantization >= 1 && p.quantization <= 1e6, 'quantization', 'Quantization must be an integer from 1 to 1,000,000.');
  requireThat(Array.isArray(p.goals) && p.goals.length > 0 && p.goals.length <= 16 && Array.isArray(p.resources) && p.resources.length <= 16, 'profile_groups', 'Use 1–16 goals and at most 16 resources.');
  const ids = new Set();
  for (const [channel, groups] of [['benefit', p.goals], ['cost', p.resources]]) {
    for (const g of groups) {
      id(g.id, 'Group ID'); requireThat(!ids.has(g.id), 'duplicate_id', 'Duplicate group: ' + g.id); ids.add(g.id);
      finite(g.scale, g.id + ' scale', 0);
      if (channel === 'benefit') finite(g.weight, g.id + ' importance', 0);
      else { finite(g.conversion, g.id + ' utility conversion', 0); requireThat(typeof g.unit === 'string' && g.unit.length > 0, 'unit', 'Resource unit is required.'); }
      requireThat(Array.isArray(g.predictors) && g.predictors.length > 0 && g.predictors.length <= 32, 'predictors', 'Each group needs 1–32 predictors.');
      const seen = new Set();
      for (const b of g.predictors) {
        id(b.id, 'Predictor ID'); requireThat(!seen.has(b.id), 'duplicate_id', 'Duplicate predictor: ' + b.id); seen.add(b.id);
        finite(b.weight, b.id + ' weight', 0); finite(b.reference, b.id + ' reference');
        requireThat(b.reference > 0, 'reference', 'Predictor reference must be positive.');
        requireThat(typeof b.unit === 'string' && b.unit.length > 0, 'unit', 'Predictor unit is required.');
        if (channel === 'benefit') requireThat(b.direction === 1 || b.direction === -1, 'direction', 'KPI direction must be 1 or -1.');
      }
      requireThat(Math.abs(g.predictors.reduce((s, b) => s + b.weight, 0) - 1) < 1e-9, 'weights', g.id + ' predictor weights must sum to 1.');
    }
  }
  requireThat(Math.abs(p.goals.reduce((s, g) => s + g.weight, 0) - 1) < 1e-9, 'weights', 'Goal importance weights must sum to 1.');
  requireThat(['linear_v1', 'custom_v1', 'custom_net_v1'].includes(p.model?.mode), 'model', 'Unsupported scoring model.');
  if (p.model.mode === 'custom_v1') {
    requireThat(typeof p.model.benefit === 'string' && p.model.costs && typeof p.model.costs === 'object' && !Array.isArray(p.model.costs), 'model', 'Custom model needs a benefit expression and resource cost expressions.');
    requireThat(Object.keys(p.model.costs).every(k => p.resources.some(r => r.id === k)), 'model', 'Custom cost references an unknown resource.');
  }
  if (p.model.mode === 'custom_net_v1') requireThat(typeof p.model.expression === 'string', 'model', 'Custom net model needs an expression.');
  return p;
}
export function defaultProfile() {
  return {schema: 'devgraph.selection-profile.v1', id: 'local-planning', version: '1', baseline: 'Current product; exclude sunk costs', horizon: 'Next planning cycle', utility_unit: 'utility', confidence_mode: 'none', blocks_policy: 'require', quantization: 1000,
    goals: [{id: 'impact', label: 'Product impact', weight: 1, scale: 100, predictors: [{id: 'kpi', label: 'KPI improvement', unit: 'KPI units', weight: 1, reference: 100, direction: 1}]}],
    resources: [{id: 'effort', label: 'Remaining effort', unit: 'points', conversion: 3, scale: 1, predictors: [{id: 'estimate', label: 'Fibonacci estimate', unit: 'points', weight: 1, reference: 1}]}], model: {mode: 'linear_v1'}};
}
