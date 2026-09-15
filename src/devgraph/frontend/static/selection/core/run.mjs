import {clone, fingerprint, requireThat, SelectionError, validateProfile} from './contracts.mjs';
import {resolveScope, validateSnapshot} from './adapter.mjs';
import {evaluateScore} from './scoring.mjs';
import {solveClosure} from './closure.mjs';

export function validateDecision(input) {
  requireThat(input?.schema === 'devgraph.selection-decision.v1', 'decision_schema', 'Unsupported decision file.');
  validateProfile(input.profile); validateSnapshot(input.snapshot);
  requireThat(input.observations && typeof input.observations === 'object' && !Array.isArray(input.observations), 'observations', 'Observations must be keyed by Work identity.');
  requireThat(Array.isArray(input.seeds), 'seeds', 'Candidate scope is required.');
  return input;
}
export function evaluateDecision(input) {
  validateDecision(input);
  const scope = resolveScope(input.snapshot, input.seeds, input.profile), scores = [], problems = [];
  for (const node of scope.nodes) {
    try { scores.push(evaluateScore(node, input.observations[node.key], input.profile)); }
    catch (e) { problems.push({key: node.key, title: node.title, code: e.code, message: e.message, details: e.details || []}); }
  }
  return {scope, scores, problems};
}
export async function runSelection(input) {
  // Detach from mutable caller state before any async boundary.
  const frozen = clone(input), {scope, scores, problems} = evaluateDecision(frozen);
  if (problems.length) throw new SelectionError('incomplete_inputs', 'Review estimates for ' + problems.length + ' work item(s).', problems);
  const solution = solveClosure(scores.map(s => ({key: s.key, utility: s.utility})), scope.dependencies, frozen.profile.quantization);
  const selected = new Set(solution.selected), selected_scores = scores.filter(s => selected.has(s.key));
  const totals = {utility: selected_scores.reduce((sum, s) => sum + s.utility, 0), benefit: selected_scores.reduce((sum, s) => sum + s.benefit, 0), burden: selected_scores.reduce((sum, s) => sum + s.burden, 0), costs: frozen.profile.resources.map(r => ({id: r.id, unit: r.unit, amount: selected_scores.reduce((sum, s) => sum + s.costs.find(c => c.id === r.id).amount, 0)}))};
  const rows = scores.map(s => ({...s, selected: selected.has(s.key), required_by: scope.dependencies.filter(d => d.prerequisite === s.key && d.dependent !== s.key && selected.has(d.dependent)).map(d => d.dependent)}));
  return {schema: 'devgraph.selection-run.v1', fingerprint: await fingerprint(frozen), input: frozen, profile_version: frozen.profile.version, rows, totals, scope: {added: scope.added, keys: scope.nodes.map(n => n.key), dependencies: scope.dependencies}, solution};
}
